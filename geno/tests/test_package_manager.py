"""
Tests for the package manager (manifest, lockfile, install/add/update)
======================================================================
"""

import contextlib
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.lockfile import (
    LockedDependency,
    Lockfile,
    compute_content_hash,
    compute_legacy_content_hash,
    parse_lockfile,
    save_lockfile,
)
from geno.manifest import (
    Dependency,
    Manifest,
    can_map_to_pascal,
    kebab_to_pascal,
    parse_manifest,
    pascal_to_kebab,
    save_manifest,
)
from geno.module_resolver import ModuleResolutionError, resolve_modules
from geno.package_manager import (
    _validate_git_commit,
    _validate_git_ref,
    _validate_git_url,
    add,
    find_project_root,
    install,
    update,
)

# =========================================================================
# Manifest tests
# =========================================================================


class TestManifestParse:
    def test_manifest_rejects_symlink(self, tmp_path):
        target = tmp_path / "target.toml"
        target.write_text('name = "outside"\n')
        manifest = tmp_path / "geno.toml"
        try:
            manifest.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        with pytest.raises(ValueError, match="must not be a symbolic link"):
            parse_manifest(manifest)

    def test_manifest_rejects_oversized_file(self, tmp_path):
        manifest = tmp_path / "geno.toml"
        manifest.write_bytes(b"#" * (1024 * 1024 + 1))
        with pytest.raises(ValueError, match="too large"):
            parse_manifest(manifest)

    def test_basic_manifest(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.entrypoint == "Main"
        assert m.files == ["Main.geno"]
        assert m.dependencies == {}

    def test_manifest_with_dependencies(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
entrypoint = "Main"
files = ["Main.geno"]

[dependencies.http-utils]
git = "https://github.com/user/geno-http-utils.git"
branch = "main"

[dependencies.math-lib]
git = "https://github.com/user/geno-math.git"
branch = "develop"
"""
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert "http-utils" in m.dependencies
        assert (
            m.dependencies["http-utils"].git
            == "https://github.com/user/geno-http-utils.git"
        )
        assert m.dependencies["http-utils"].branch == "main"
        assert m.dependencies["math-lib"].branch == "develop"

    def test_manifest_default_branch(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.foo]
git = "https://example.com/foo.git"
"""
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.dependencies["foo"].branch == "main"

    def test_manifest_invalid_dependency_name_raises(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies."../../evil"]
git = "https://example.com/evil.git"
"""
        )
        with pytest.raises(ValueError, match="Invalid dependency name"):
            parse_manifest(tmp_path / "geno.toml")

    @pytest.mark.parametrize("name", ["bad;name", "foo-", "a--b", "lib-2"])
    def test_manifest_codegen_unsafe_dependency_name_raises(self, tmp_path, name):
        (tmp_path / "geno.toml").write_text(
            f"""
[dependencies."{name}"]
git = "https://example.com/evil.git"
"""
        )

        with pytest.raises(ValueError, match="Invalid dependency name"):
            parse_manifest(tmp_path / "geno.toml")

    def test_manifest_dotted_dependency_name_raises(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies."foo.bar"]
git = "https://example.com/foo.git"
"""
        )
        with pytest.raises(ValueError, match="Invalid dependency name"):
            parse_manifest(tmp_path / "geno.toml")

    @pytest.mark.parametrize("name", ["CON", "nul", "COM1", "LPT9", "C:foo"])
    def test_manifest_filesystem_sensitive_dependency_name_raises(self, tmp_path, name):
        (tmp_path / "geno.toml").write_text(
            f"""
[dependencies."{name}"]
git = "https://example.com/foo.git"
"""
        )

        with pytest.raises(ValueError, match="Invalid dependency name"):
            parse_manifest(tmp_path / "geno.toml")

    @pytest.mark.parametrize(
        "entrypoint",
        ["../victim/Internal", "src/Main", "C:Main", "", "main"],
    )
    def test_manifest_invalid_entrypoint_raises(self, tmp_path, entrypoint):
        (tmp_path / "geno.toml").write_text(f'entrypoint = "{entrypoint}"\n')

        with pytest.raises(ValueError, match="Invalid manifest entrypoint"):
            parse_manifest(tmp_path / "geno.toml")

    def test_manifest_missing_git_raises(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.bad]
branch = "main"
"""
        )
        with pytest.raises(ValueError, match="must have a 'git' key"):
            parse_manifest(tmp_path / "geno.toml")

    @pytest.mark.parametrize(
        ("contents", "message"),
        [
            ("entrypoint = 1\n", "entrypoint.*string"),
            ("name = 1\n", "name.*string"),
            ("version = 1\n", "version.*string"),
            ('files = "Main"\n', "files.*list of strings"),
            ('files = ["Main", 2]\n', "files.*list of strings"),
            ('targets = "browser"\n', "targets.*list of strings"),
            ("target = 42\n", "target.*string"),
            ('exports = "Math"\n', "exports.*table"),
            ('[exports]\nmodules = "Math"\n', "exports.modules.*list of strings"),
        ],
    )
    def test_manifest_known_fields_validate_types(self, tmp_path, contents, message):
        (tmp_path / "geno.toml").write_text(contents)

        with pytest.raises(ValueError, match=message):
            parse_manifest(tmp_path / "geno.toml")

    @pytest.mark.parametrize(
        ("contents", "message"),
        [
            ("dependencies = []\n", "dependencies.*table"),
            ("[dependencies.bad]\ngit = 42\n", "git.*string"),
            (
                '[dependencies.bad]\ngit = "https://example.com/bad.git"\n'
                "branch = 42\n",
                "branch.*string",
            ),
            (
                '[dependencies.bad]\ngit = "https://example.com/bad.git"\ntag = 42\n',
                "tag.*string",
            ),
        ],
    )
    def test_manifest_dependency_fields_validate_types(
        self, tmp_path, contents, message
    ):
        (tmp_path / "geno.toml").write_text(contents)

        with pytest.raises(ValueError, match=message):
            parse_manifest(tmp_path / "geno.toml")


class TestManifestSave:
    def test_round_trip(self, tmp_path):
        manifest = Manifest(
            entrypoint="Main",
            files=["Main.geno"],
            dependencies={
                "utils": Dependency(
                    name="utils",
                    git="https://example.com/utils.git",
                    branch="main",
                )
            },
        )
        save_manifest(manifest, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        assert reloaded.entrypoint == "Main"
        assert reloaded.files == ["Main.geno"]
        assert "utils" in reloaded.dependencies
        assert reloaded.dependencies["utils"].git == "https://example.com/utils.git"

    def test_save_non_default_branch(self, tmp_path):
        manifest = Manifest(
            dependencies={
                "lib": Dependency(name="lib", git="https://x.com/lib.git", branch="dev")
            }
        )
        save_manifest(manifest, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert 'branch = "dev"' in text

    def test_save_default_branch_omitted(self, tmp_path):
        manifest = Manifest(
            dependencies={
                "lib": Dependency(
                    name="lib", git="https://x.com/lib.git", branch="main"
                )
            }
        )
        save_manifest(manifest, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert "branch" not in text

    def test_save_rejects_unsafe_dependency_table_keys(self, tmp_path):
        manifest = Manifest(
            dependencies={
                "my lib": Dependency(
                    name="my lib",
                    git="https://example.com/my-lib.git",
                    branch="main",
                )
            }
        )

        with pytest.raises(ValueError, match="Invalid dependency name"):
            save_manifest(manifest, tmp_path / "geno.toml")


class TestManifestNewFields:
    """Tests for name, version, targets, exports, and dependency tag fields."""

    def test_name_and_version(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'name = "my-project"\nversion = "0.1.0"\nentrypoint = "Main"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.name == "my-project"
        assert m.version == "0.1.0"

    def test_targets(self, tmp_path):
        (tmp_path / "geno.toml").write_text('targets = ["python-cli", "browser"]\n')
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.targets == ["python-cli", "browser"]

    def test_exports_modules(self, tmp_path):
        (tmp_path / "geno.toml").write_text('[exports]\nmodules = ["Math", "Utils"]\n')
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.exports == ["Math", "Utils"]

    def test_dependency_tag(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            '[dependencies.foo]\ngit = "https://example.com/foo.git"\ntag = "v0.3.0"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.dependencies["foo"].tag == "v0.3.0"

    def test_defaults_for_optional_fields(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.name is None
        assert m.version is None
        assert m.targets == []
        assert m.exports == []

    def test_save_round_trip_all_fields(self, tmp_path):
        manifest = Manifest(
            name="my-lib",
            version="1.2.3",
            entrypoint="Main",
            files=["Main"],
            targets=["python-cli", "node-cli"],
            exports=["Math", "Utils"],
            dependencies={
                "dep": Dependency(
                    name="dep",
                    git="https://example.com/dep.git",
                    tag="v0.3.0",
                )
            },
        )
        save_manifest(manifest, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        assert reloaded.name == "my-lib"
        assert reloaded.version == "1.2.3"
        assert reloaded.targets == ["python-cli", "node-cli"]
        assert reloaded.exports == ["Math", "Utils"]
        assert reloaded.dependencies["dep"].tag == "v0.3.0"

    def test_backward_compatible_no_new_fields(self, tmp_path):
        """Old manifests without new fields still parse correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\n'
            '[dependencies.foo]\ngit = "https://example.com/foo.git"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.entrypoint == "Main"
        assert m.dependencies["foo"].tag is None

    def test_singular_target_backward_compat(self, tmp_path):
        """Old manifests with singular target = '...' still parse."""
        (tmp_path / "geno.toml").write_text(
            'target = "python-cli"\nentrypoint = "Main"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        assert m.targets == ["python-cli"]


class TestManifestPreserveUnknownKeys:
    """Tests for preserving unknown TOML keys across save_manifest round-trips."""

    def test_unknown_scalar_key_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\ncustom_key = "keep_me"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert 'custom_key = "keep_me"' in text

    def test_quoted_unknown_scalar_key_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n"custom.key" = "keep_me"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert '"custom.key" = "keep_me"' in text
        assert reloaded._raw.get("custom.key") == "keep_me"

    def test_unknown_table_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n\n[metadata]\nauthor = "Alice"\nlicense = "MIT"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert "[metadata]" in text
        assert 'author = "Alice"' in text
        assert 'license = "MIT"' in text

    def test_nested_unknown_table_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n\n[metadata.author]\nname = "Alice"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert "[metadata.author]" in text
        assert (
            reloaded._raw.get("metadata", {}).get("author", {}).get("name") == "Alice"
        )

    def test_unknown_keys_survive_full_round_trip(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'name = "proj"\nversion = "1.0.0"\nentrypoint = "Main"\n'
            "custom_flag = true\n\n"
            '[metadata]\nauthor = "Bob"\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        assert reloaded.name == "proj"
        assert reloaded.entrypoint == "Main"
        assert reloaded._raw.get("custom_flag") is True
        assert reloaded._raw.get("metadata", {}).get("author") == "Bob"

    def test_unknown_int_and_list_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nmax_retries = 3\ntags = ["alpha", "beta"]\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert "max_retries = 3" in text
        assert 'tags = ["alpha", "beta"]' in text

    def test_unknown_datetime_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nreleased = 2026-04-12T19:00:00Z\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        released = reloaded._raw.get("released")
        assert released is not None
        assert released.isoformat() == "2026-04-12T19:00:00+00:00"

    def test_unknown_list_of_inline_tables_preserved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nplugins = [{ name = "a" }, { name = "b" }]\n'
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        reloaded = parse_manifest(tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert 'plugins = [{ name = "a" }, { name = "b" }]' in text
        assert reloaded._raw.get("plugins") == [{"name": "a"}, {"name": "b"}]

    def test_known_keys_not_duplicated(self, tmp_path):
        (tmp_path / "geno.toml").write_text('name = "proj"\nentrypoint = "Main"\n')
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        assert text.count("name") == 1
        assert text.count("entrypoint") == 1

    def test_nested_table_preserved(self, tmp_path):
        """Nested tables like [ci.build] must round-trip correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n\n'
            "[ci]\n"
            'runner = "ubuntu"\n\n'
            "[ci.build]\n"
            'opt = "release"\n'
            "parallel = true\n"
        )
        m = parse_manifest(tmp_path / "geno.toml")
        save_manifest(m, tmp_path / "geno.toml")
        text = (tmp_path / "geno.toml").read_text()
        # Verify nested table structure is preserved
        assert "[ci]" in text
        assert 'runner = "ubuntu"' in text
        assert "[ci.build]" in text
        assert 'opt = "release"' in text
        assert "parallel = true" in text
        # Verify the nested value isn't mangled (e.g., dict repr instead of table)
        assert "{'opt'" not in text


# =========================================================================
# Lockfile tests
# =========================================================================


class TestLockfileParse:
    def test_lockfile_rejects_symlink(self, tmp_path):
        target = tmp_path / "target.lock"
        target.write_text("[dependencies]\n")
        lockfile = tmp_path / "geno.lock"
        try:
            lockfile.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        with pytest.raises(ValueError, match="must not be a symbolic link"):
            parse_lockfile(lockfile)

    def test_lockfile_rejects_oversized_file(self, tmp_path):
        lockfile = tmp_path / "geno.lock"
        lockfile.write_bytes(b"#" * (1024 * 1024 + 1))
        with pytest.raises(ValueError, match="too large"):
            parse_lockfile(lockfile)

    def test_missing_file_returns_empty(self, tmp_path):
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert lf.dependencies == {}

    def test_basic_lockfile(self, tmp_path):
        (tmp_path / "geno.lock").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
commit = "abcdef1234567890abcdef1234567890abcdef12"
branch = "main"
"""
        )
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert "utils" in lf.dependencies
        assert (
            lf.dependencies["utils"].commit
            == "abcdef1234567890abcdef1234567890abcdef12"
        )

    def test_lockfile_allows_sha256_object_id(self, tmp_path):
        commit = "a" * 64
        (tmp_path / "geno.lock").write_text(
            f"""
[dependencies.utils]
git = "https://example.com/utils.git"
commit = "{commit}"
branch = "main"
"""
        )

        lf = parse_lockfile(tmp_path / "geno.lock")

        assert lf.dependencies["utils"].commit == commit

    @pytest.mark.parametrize(
        ("contents", "message"),
        [
            ("dependencies = []\n", "dependencies.*table"),
            (
                '[dependencies.bad]\ngit = 42\ncommit = "abcdef1234567890abcdef1234567890abcdef12"\n',
                "git.*string",
            ),
            ('[dependencies.bad]\ngit = "https://x/y.git"\n', "commit"),
            (
                '[dependencies."../bad"]\ngit = "https://x/y.git"\ncommit = "abcdef1234567890abcdef1234567890abcdef12"\n',
                "Invalid dependency name",
            ),
            (
                "[dependencies.bad]\n"
                'git = "https://x/y.git"\n'
                'commit = "not-a-commit"\n',
                "commit.*hex",
            ),
            (
                "[dependencies.bad]\n"
                'git = "https://x/y.git"\n'
                'commit = "abcdef1234567890abcdef1234567890abcdef12"\n'
                'branch = "../main"\n',
                "git ref.*unsafe",
            ),
            (
                "[dependencies.bad]\n"
                'git = "https://x/y.git"\n'
                'commit = "abcdef1234567890abcdef1234567890abcdef12"\n'
                'content_hash = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"\n',
                "content_hash.*SHA-256",
            ),
        ],
    )
    def test_lockfile_dependency_fields_validate_types(
        self, tmp_path, contents, message
    ):
        (tmp_path / "geno.lock").write_text(contents)

        with pytest.raises(ValueError, match=message):
            parse_lockfile(tmp_path / "geno.lock")


class TestLockfileSave:
    def test_round_trip(self, tmp_path):
        lockfile = Lockfile(
            dependencies={
                "foo": LockedDependency(
                    name="foo",
                    git="https://example.com/foo.git",
                    commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                    branch="main",
                )
            }
        )
        save_lockfile(lockfile, tmp_path / "geno.lock")
        reloaded = parse_lockfile(tmp_path / "geno.lock")
        assert (
            reloaded.dependencies["foo"].commit
            == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        )
        assert reloaded.dependencies["foo"].git == "https://example.com/foo.git"

    def test_save_rejects_unsafe_dependency_table_keys(self, tmp_path):
        lockfile = Lockfile(
            dependencies={
                "my lib": LockedDependency(
                    name="my lib",
                    git="https://example.com/my-lib.git",
                    commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                )
            }
        )

        with pytest.raises(ValueError, match="Invalid dependency name"):
            save_lockfile(lockfile, tmp_path / "geno.lock")


class TestLockfileContentHash:
    def test_content_hash_saved_and_loaded(self, tmp_path):
        lockfile = Lockfile(
            dependencies={
                "foo": LockedDependency(
                    name="foo",
                    git="https://example.com/foo.git",
                    commit="abcdef1234567890abcdef1234567890abcdef12",
                    content_hash="a" * 64,
                )
            }
        )
        save_lockfile(lockfile, tmp_path / "geno.lock")
        reloaded = parse_lockfile(tmp_path / "geno.lock")
        assert reloaded.dependencies["foo"].content_hash == "a" * 64
        assert reloaded.dependencies["foo"].content_hash_version == 2

    def test_content_hash_omitted_when_empty(self, tmp_path):
        lockfile = Lockfile(
            dependencies={
                "foo": LockedDependency(
                    name="foo",
                    git="https://example.com/foo.git",
                    commit="abcdef1234567890abcdef1234567890abcdef12",
                )
            }
        )
        save_lockfile(lockfile, tmp_path / "geno.lock")
        text = (tmp_path / "geno.lock").read_text()
        assert "content_hash" not in text

    def test_pre_v2_content_hash_parses_as_legacy(self, tmp_path):
        path = tmp_path / "geno.lock"
        path.write_text(
            "[dependencies.foo]\n"
            'git = "https://example.com/foo.git"\n'
            f'commit = "{"a" * 40}"\n'
            'branch = "main"\n'
            f'content_hash = "{"B" * 64}"\n'
        )

        locked = parse_lockfile(path).dependencies["foo"]

        assert locked.content_hash_version == 1
        assert locked.content_hash == "b" * 64

    @pytest.mark.parametrize("toml_value", ["true", "0", "3", '"2"'])
    def test_invalid_content_hash_version_is_rejected(self, tmp_path, toml_value):
        path = tmp_path / "geno.lock"
        path.write_text(
            "[dependencies.foo]\n"
            'git = "https://example.com/foo.git"\n'
            f'commit = "{"a" * 40}"\nbranch = "main"\ncontent_hash = "{"b" * 64}"\n'
            f"content_hash_version = {toml_value}\n"
        )
        with pytest.raises(ValueError, match="content_hash_version"):
            parse_lockfile(path)

    def test_compute_content_hash(self, tmp_path):
        (tmp_path / "Lib.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        (tmp_path / "geno.toml").write_text('files = ["Lib"]\n')
        h = compute_content_hash(tmp_path)
        assert len(h) == 64  # SHA-256 hex digest
        # Deterministic
        assert h == compute_content_hash(tmp_path)

    def test_compute_content_hash_includes_dependency_tree_files(self, tmp_path):
        (tmp_path / "Lib.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        h1 = compute_content_hash(tmp_path)

        (tmp_path / "helper.py").write_text("print('hello')\n")
        h2 = compute_content_hash(tmp_path)
        assert h2 != h1

        scripts = tmp_path / "scripts"
        scripts.mkdir()
        script = scripts / "build.sh"
        script.write_text("#!/bin/sh\necho build\n")
        h3 = compute_content_hash(tmp_path)
        assert h3 != h2

        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "logo.bin").write_bytes(b"\x00GENO\xff")
        h4 = compute_content_hash(tmp_path)
        assert h4 != h3

    def test_compute_content_hash_frames_binary_records_unambiguously(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()

        # These trees collided when raw content and metadata were separated only
        # by NUL bytes because file content can contain the delimiter itself.
        (first / "a").write_bytes(b"X\0b\0file\0executable=0\0Y")
        (second / "a").write_bytes(b"X")
        (second / "b").write_bytes(b"Y")

        assert compute_content_hash(first) != compute_content_hash(second)
        assert compute_legacy_content_hash(first) == compute_legacy_content_hash(second)

    def test_compute_content_hash_ignores_git_metadata(self, tmp_path):
        (tmp_path / "Lib.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        h1 = compute_content_hash(tmp_path)

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "index").write_bytes(b"local git metadata")

        assert compute_content_hash(tmp_path) == h1

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "Lib.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        h2 = compute_content_hash(worktree)
        (worktree / ".git").write_text("gitdir: ../.git/worktrees/lib\n")
        assert compute_content_hash(worktree) == h2

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is unavailable on this platform",
    )
    def test_compute_content_hash_records_internal_symlink_and_target(self, tmp_path):
        (tmp_path / "Lib.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        targets = tmp_path / "targets"
        targets.mkdir()
        first = targets / "first.txt"
        first.write_text("first")
        link = tmp_path / "linked.txt"
        try:
            os.symlink(str(Path("targets") / "first.txt"), link)
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")
        h1 = compute_content_hash(tmp_path)

        first.write_text("second")
        h2 = compute_content_hash(tmp_path)
        assert h2 != h1

        second = targets / "second.txt"
        second.write_text("second")
        link.unlink()
        os.symlink(str(Path("targets") / "second.txt"), link)
        h3 = compute_content_hash(tmp_path)
        assert h3 != h2

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is unavailable on this platform",
    )
    def test_compute_content_hash_rejects_escaping_and_dangling_symlinks(
        self, tmp_path
    ):
        outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        outside.write_text("outside")
        link = tmp_path / "linked.txt"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        with pytest.raises(RuntimeError, match="escapes its checkout or is dangling"):
            compute_content_hash(tmp_path)

        link.unlink()
        os.symlink("missing.txt", link)
        with pytest.raises(RuntimeError, match="escapes its checkout or is dangling"):
            compute_content_hash(tmp_path)

    @pytest.mark.skipif(
        not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable"
    )
    def test_compute_content_hash_rejects_unsupported_filesystem_entry(self, tmp_path):
        fifo = tmp_path / "FutureModule.geno"
        mkfifo = getattr(os, "mkfifo", None)
        assert callable(mkfifo)
        mkfifo(fifo)

        with pytest.raises(RuntimeError, match="Unsupported filesystem entry"):
            compute_content_hash(tmp_path)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits only")
    def test_compute_content_hash_records_executable_bit(self, tmp_path):
        script = tmp_path / "script.sh"
        script.write_text("#!/bin/sh\necho ok\n")
        h1 = compute_content_hash(tmp_path)

        script.chmod(0o755)
        h2 = compute_content_hash(tmp_path)
        assert h2 != h1


# =========================================================================
# Project root discovery
# =========================================================================


class TestFindProjectRoot:
    def test_finds_root_in_current(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')
        root = find_project_root(tmp_path)
        assert root == tmp_path.resolve()

    def test_finds_root_from_subdirectory(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')
        subdir = tmp_path / "src" / "nested"
        subdir.mkdir(parents=True)
        root = find_project_root(subdir)
        assert root == tmp_path.resolve()

    def test_raises_when_no_root(self, tmp_path):
        with pytest.raises(FileNotFoundError, match=r"No geno\.toml"):
            find_project_root(tmp_path)


# =========================================================================
# Dependency commands
# =========================================================================


class TestDependencyCommands:
    def test_add_rejects_unsafe_dependency_table_keys(self, tmp_path):
        original = 'entrypoint = "Main"\n'
        (tmp_path / "geno.toml").write_text(original)
        installed: list[Path] = []

        from geno import package_manager

        with mock.patch.object(
            package_manager,
            "_install_locked",
            side_effect=lambda root: installed.append(root),
        ):
            with pytest.raises(ValueError, match="Invalid dependency name"):
                package_manager.add(
                    "my lib",
                    "https://example.com/my-lib.git",
                    project_root=tmp_path,
                )

        assert installed == []
        assert (tmp_path / "geno.toml").read_text() == original

    def test_add_tag_dependency_writes_tag(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')
        installed: list[Path] = []

        from geno import package_manager

        with mock.patch.object(
            package_manager,
            "_install_locked",
            side_effect=lambda root: installed.append(root),
        ):
            package_manager.add(
                "utils",
                "https://example.com/utils.git",
                tag="v0.1.0",
                project_root=tmp_path,
            )

        text = (tmp_path / "geno.toml").read_text()
        reloaded = parse_manifest(tmp_path / "geno.toml")
        dep = reloaded.dependencies["utils"]
        assert installed == [tmp_path.resolve()]
        assert 'tag = "v0.1.0"' in text
        assert "branch =" not in text
        assert dep.git == "https://example.com/utils.git"
        assert dep.tag == "v0.1.0"
        assert dep.branch == "main"

    def test_add_rejects_invalid_tag_before_install(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')

        from geno import package_manager

        with mock.patch.object(package_manager, "install") as install_mock:
            with pytest.raises(ValueError, match="Invalid git tag"):
                package_manager.add(
                    "utils",
                    "https://example.com/utils.git",
                    tag="-bad",
                    project_root=tmp_path,
                )

        install_mock.assert_not_called()

    def test_add_rejects_branch_and_tag_combination_before_install(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\n')

        from geno import package_manager

        with mock.patch.object(package_manager, "install") as install_mock:
            with pytest.raises(ValueError, match="both a branch and a tag"):
                package_manager.add(
                    "utils",
                    "https://example.com/utils.git",
                    branch="develop",
                    tag="v0.1.0",
                    project_root=tmp_path,
                )

        install_mock.assert_not_called()


# =========================================================================
# Module resolution with geno_modules/
# =========================================================================


class TestModuleResolutionWithGenoModules:
    def test_resolves_from_geno_modules(self, tmp_path):
        """import Utils resolves to geno_modules/Utils/Utils.geno"""
        # Set up project
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            """
import Utils

func main() -> Int
  return double(3)
end func
"""
        )

        # Set up geno_modules/Utils/Utils.geno
        utils_dir = tmp_path / "geno_modules" / "Utils"
        utils_dir.mkdir(parents=True)
        (utils_dir / "Utils.geno").write_text(
            """
func double(x: Int) -> Int
  return x * 2
end func
"""
        )

        from geno.lexer import Lexer
        from geno.parser import Parser

        source = (tmp_path / "Main.geno").read_text()
        tokens = Lexer(source, str(tmp_path / "Main.geno")).tokenize()
        program = Parser(tokens).parse_program()
        modules = resolve_modules(tmp_path / "Main.geno", program)

        assert "Utils" in modules
        assert "double" in modules["Utils"]

    def test_resolves_entrypoint_from_dep_manifest(self, tmp_path):
        """Dependency with a geno.toml entrypoint different from directory name."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            """
import MyLib

func main() -> Int
  return 42
end func
"""
        )

        lib_dir = tmp_path / "geno_modules" / "MyLib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "geno.toml").write_text('entrypoint = "Lib"\n')
        (lib_dir / "Lib.geno").write_text(
            """
func helper() -> Int
  return 1
end func
"""
        )

        from geno.lexer import Lexer
        from geno.parser import Parser

        source = (tmp_path / "Main.geno").read_text()
        tokens = Lexer(source, str(tmp_path / "Main.geno")).tokenize()
        program = Parser(tokens).parse_program()
        modules = resolve_modules(tmp_path / "Main.geno", program)

        assert "MyLib" in modules
        assert "helper" in modules["MyLib"]

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is unavailable on this platform",
    )
    def test_module_resolver_rejects_escaping_dependency_manifest(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        main_path = tmp_path / "Main.geno"
        main_path.write_text("import MyLib\n")
        lib_dir = tmp_path / "geno_modules" / "MyLib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "Lib.geno").write_text(
            "func helper() -> Int\n  return 1\nend func\n"
        )
        outside_manifest = tmp_path / "outside-geno.toml"
        outside_manifest.write_text('entrypoint = "Lib"\n')
        try:
            os.symlink(outside_manifest, lib_dir / "geno.toml")
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        from geno.lexer import Lexer
        from geno.parser import Parser

        program = Parser(
            Lexer(main_path.read_text(), str(main_path)).tokenize()
        ).parse_program()
        with pytest.raises(ModuleResolutionError, match="Module 'MyLib' not found"):
            resolve_modules(main_path, program)

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is unavailable on this platform",
    )
    def test_project_graph_rejects_escaping_dependency_manifest(self, tmp_path):
        from geno.project_graph import ProjectGraph, ProjectGraphError

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
            '[dependencies.my-lib]\ngit = "https://example.com/my-lib.git"\n'
        )
        main_path = tmp_path / "Main.geno"
        main_path.write_text("func main() -> Int\n  return 0\nend func\n")
        lib_dir = tmp_path / "geno_modules" / "my-lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "Lib.geno").write_text(
            "func helper() -> Int\n  return 1\nend func\n"
        )
        outside_manifest = tmp_path / "outside-graph-geno.toml"
        outside_manifest.write_text('entrypoint = "Lib"\n')
        try:
            os.symlink(outside_manifest, lib_dir / "geno.toml")
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        with pytest.raises(ProjectGraphError, match="manifest escapes package root"):
            ProjectGraph.discover(main_path)

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is unavailable on this platform",
    )
    def test_project_graph_accepts_internal_dependency_manifest_symlink(self, tmp_path):
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
            '[dependencies.my-lib]\ngit = "https://example.com/my-lib.git"\n'
        )
        main_path = tmp_path / "Main.geno"
        main_path.write_text("func main() -> Int\n  return 0\nend func\n")
        lib_dir = tmp_path / "geno_modules" / "my-lib"
        metadata = lib_dir / "metadata"
        metadata.mkdir(parents=True)
        (metadata / "manifest.toml").write_text('entrypoint = "Lib"\n')
        (lib_dir / "Lib.geno").write_text(
            "func helper() -> Int\n  return 1\nend func\n"
        )
        try:
            os.symlink(str(Path("metadata") / "manifest.toml"), lib_dir / "geno.toml")
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")

        graph = ProjectGraph.discover(main_path)
        assert any(
            file.path == (lib_dir / "Lib.geno").resolve() for file in graph.files
        )

    def test_rejects_dependency_entrypoint_sibling_traversal(self, tmp_path):
        """A dependency manifest cannot redirect imports into a sibling package."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            """
import MyLib

func main() -> Int
  return 42
end func
"""
        )

        lib_dir = tmp_path / "geno_modules" / "MyLib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "geno.toml").write_text('entrypoint = "../victim/Internal"\n')
        victim_dir = tmp_path / "geno_modules" / "victim"
        victim_dir.mkdir()
        (victim_dir / "Internal.geno").write_text(
            """
func helper() -> Int
  return 1
end func
"""
        )

        from geno.lexer import Lexer
        from geno.parser import Parser

        source = main_path.read_text()
        tokens = Lexer(source, str(main_path)).tokenize()
        program = Parser(tokens).parse_program()

        with pytest.raises(ModuleResolutionError, match="Module 'MyLib' not found"):
            resolve_modules(main_path, program)


# =========================================================================
# Install with mocked git
# =========================================================================


class TestInstallMocked:
    def test_install_clones_tag_dependency(self, tmp_path):
        """Fresh tag dependencies should clone the tag ref, not a branch."""
        (tmp_path / "geno.toml").write_text(
            """
entrypoint = "Main"
files = ["Main.geno"]

[dependencies.utils]
git = "https://example.com/utils.git"
tag = "v0.3.0"
"""
        )
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[0] == "git" and cmd[1] == "clone":
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        clone_cmd = next(
            command for command in commands if command[:2] == ["git", "clone"]
        )
        assert clone_cmd[:-1] == [
            "git",
            "clone",
            "--branch",
            "v0.3.0",
            "--single-branch",
            "--depth",
            "1",
            "https://example.com/utils.git",
        ]
        assert Path(clone_cmd[-1]).name == "utils"
        assert Path(clone_cmd[-1]).parent.name == "staged"
        lf = parse_lockfile(tmp_path / "geno.lock")
        locked = lf.dependencies["utils"]
        assert locked.tag == "v0.3.0"
        assert locked.branch == "main"
        assert locked.commit == "abcdef1234567890abcdef1234567890abcdef12"
        assert len(locked.content_hash) == 64

    def test_install_clones_dependency(self, tmp_path):
        """Install should clone deps that don't exist yet."""
        (tmp_path / "geno.toml").write_text(
            """
entrypoint = "Main"
files = ["Main.geno"]

[dependencies.utils]
git = "https://example.com/utils.git"
branch = "main"
"""
        )

        def fake_run(cmd, **kwargs):
            # Simulate git clone by creating the directory with a file
            if cmd[0] == "git" and cmd[1] == "clone":
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")
            # Simulate git rev-parse HEAD
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        assert (tmp_path / "geno.lock").exists()
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert (
            lf.dependencies["utils"].commit
            == "abcdef1234567890abcdef1234567890abcdef12"
        )
        # Content hash should be populated
        assert lf.dependencies["utils"].content_hash != ""
        assert len(lf.dependencies["utils"].content_hash) == 64

    def test_install_skips_already_installed(self, tmp_path):
        """If dep dir is already at the locked commit with matching hash, skip."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        # Pre-existing module dir and lock
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func x() -> Int\n  return 1\nend func\n")
        actual_hash = compute_content_hash(dep_dir)
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        content_hash=actual_hash,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "geno.package_manager.subprocess.run", side_effect=fake_run
        ) as mock_run:
            installed = install(tmp_path)

        assert installed == []
        # Only rev-parse should be called — no fetch/clone/checkout
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert "clone" not in cmd, "should not clone already-installed dep"
            assert "fetch" not in cmd, "should not fetch already-installed dep"

    @pytest.mark.parametrize("installed_head", ["different", "unverifiable"])
    def test_install_restages_matching_tree_without_verified_commit(
        self, tmp_path, installed_head
    ):
        """A matching tree must still have a verified, exactly pinned HEAD."""
        locked_commit = "a" * 40
        installed_commit = "b" * 40
        source = "func x() -> Int\n  return 1\nend func\n"
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text(source)
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit=locked_commit,
                        content_hash=compute_content_hash(dep_dir),
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        commands: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                staged = Path(cmd[-1])
                staged.mkdir(parents=True, exist_ok=True)
                (staged / "Utils.geno").write_text(source)
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_head(repo):
            if Path(repo) != dep_dir:
                return locked_commit
            if installed_head == "unverifiable":
                raise RuntimeError("not a Git checkout")
            return installed_commit

        with (
            mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run),
            mock.patch("geno.package_manager._git_head_commit", side_effect=fake_head),
        ):
            installed = install(tmp_path)

        assert installed == ["utils"]
        assert [
            "git",
            "-C",
            mock.ANY,
            "checkout",
            "--force",
            locked_commit,
        ] in commands
        assert parse_lockfile(tmp_path / "geno.lock").dependencies["utils"].commit == (
            locked_commit
        )

    def test_install_detects_dirty_working_tree(self, tmp_path):
        """If HEAD matches but content_hash drifts, install should not skip."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        source_file = dep_dir / "Utils.geno"
        clean_source = "func x() -> Int\n  return 1\nend func\n"
        source_file.write_text(clean_source)
        clean_hash = compute_content_hash(dep_dir)
        source_file.write_text("locally modified\n")
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        content_hash=clean_hash,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(clean_source)
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "geno.package_manager.subprocess.run", side_effect=fake_run
        ) as mock_run:
            installed = install(tmp_path)

        # Should NOT have skipped — content hash mismatch triggers re-checkout
        assert "utils" in installed
        # Verify checkout was issued; the pinned commit is already present.
        cmds = [call[0][0] for call in mock_run.call_args_list]
        assert any("checkout" in cmd for cmd in cmds), "should checkout on dirty tree"

    def test_install_restores_dirty_dependency_before_relocking(self, tmp_path):
        """Dirty locked dependency contents must not be saved back to geno.lock."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        source_file = dep_dir / "Utils.geno"
        clean_source = "func x() -> Int\n  return 1\nend func\n"
        source_file.write_text(clean_source)
        clean_hash = compute_content_hash(dep_dir)
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        content_hash=clean_hash,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        source_file.write_text("func x() -> Int\n  return 99\nend func\n")
        extra_file = dep_dir / "Extra.geno"
        extra_file.write_text("func extra() -> Int\n  return 2\nend func\n")
        assert compute_content_hash(dep_dir) != clean_hash
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if len(cmd) > 1 and cmd[1] == "clone":
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(clean_source)
            if "rev-parse" in cmd:
                if "--is-shallow-repository" in cmd:
                    return mock.Mock(returncode=0, stdout="false\n", stderr="")
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        assert source_file.read_text() == clean_source
        assert not extra_file.exists()
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert lf.dependencies["utils"].content_hash == clean_hash
        staged = Path(next(cmd for cmd in commands if cmd[:2] == ["git", "clone"])[-1])
        assert [
            "git",
            "-C",
            str(staged),
            "checkout",
            "--force",
            "abcdef1234567890abcdef1234567890abcdef12",
        ] in commands
        assert ["git", "-C", str(staged), "clean", "-ffdx"] in commands

    def test_install_backfills_missing_content_hash(self, tmp_path):
        """Old lockfiles without content_hash are checked out before backfill."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func x() -> Int\n  return 1\nend func\n")
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(
                    "func x() -> Int\n  return 1\nend func\n"
                )
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert len(lf.dependencies["utils"].content_hash) == 64
        staged = Path(next(cmd for cmd in commands if cmd[:2] == ["git", "clone"])[-1])
        assert [
            "git",
            "-C",
            str(staged),
            "checkout",
            "--force",
            "abcdef1234567890abcdef1234567890abcdef12",
        ] in commands
        assert ["git", "-C", str(staged), "clean", "-ffdx"] in commands

    def test_install_restores_dirty_old_lockfile_before_backfill(self, tmp_path):
        """Missing content_hash must not cause dirty contents to become trusted."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        source_file = dep_dir / "Utils.geno"
        clean_source = "func x() -> Int\n  return 1\nend func\n"
        dirty_source = "func x() -> Int\n  return 99\nend func\n"
        source_file.write_text(clean_source)
        clean_hash = compute_content_hash(dep_dir)
        source_file.write_text(dirty_source)
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        def fake_run(cmd, **kwargs):
            if len(cmd) > 1 and cmd[1] == "clone":
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(clean_source)
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        assert source_file.read_text() == clean_source
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert lf.dependencies["utils"].content_hash == clean_hash

    def test_install_fresh_clone_uses_locked_commit(self, tmp_path):
        """Fresh installs should use the lockfile commit, not just the manifest ref."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
branch = "main"
"""
        )
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        branch="main",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        clone_cmd = next(cmd for cmd in commands if cmd[:2] == ["git", "clone"])
        assert "--depth" not in clone_cmd
        assert clone_cmd[-2] == "https://example.com/utils.git"
        staged = Path(clone_cmd[-1])
        assert staged.name == "utils"
        assert staged.parent.name == "staged"
        assert [
            "git",
            "-C",
            str(staged),
            "checkout",
            "--force",
            "abcdef1234567890abcdef1234567890abcdef12",
        ] in commands
        lf = parse_lockfile(tmp_path / "geno.lock")
        assert (
            lf.dependencies["utils"].commit
            == "abcdef1234567890abcdef1234567890abcdef12"
        )

    def test_install_fresh_clone_verifies_locked_content_hash(self, tmp_path):
        commit = "abcdef1234567890abcdef1234567890abcdef12"
        locked_hash = "0" * 64
        (tmp_path / "geno.toml").write_text(
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit=commit,
                        content_hash=locked_hash,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "clone"]:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
            if "rev-parse" in cmd:
                return mock.Mock(returncode=0, stdout=f"{commit}\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match=r"does not match geno.lock"):
                install(tmp_path)

        reloaded = parse_lockfile(tmp_path / "geno.lock")
        assert reloaded.dependencies["utils"].content_hash == locked_hash
        assert not (tmp_path / "geno_modules" / "utils").exists()
        assert not list((tmp_path / "geno_modules").glob(".geno-package-txn-*"))

    def test_install_migrates_matching_pre_v2_content_hash(self, tmp_path):
        commit = "abcdef1234567890abcdef1234567890abcdef12"
        source = tmp_path / "legacy-source"
        source.mkdir()
        source_text = "func foo() -> Int\n  return 1\nend func\n"
        (source / "Utils.geno").write_text(source_text)
        legacy_hash = compute_legacy_content_hash(source)
        (tmp_path / "geno.toml").write_text(
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        (tmp_path / "geno.lock").write_text(
            "[dependencies.utils]\n"
            'git = "https://example.com/utils.git"\n'
            f'commit = "{commit}"\n'
            'branch = "main"\n'
            f'content_hash = "{legacy_hash}"\n'
        )

        installed_checkout = tmp_path / "geno_modules" / "utils"
        installed_checkout.mkdir(parents=True)
        (installed_checkout / "Utils.geno").write_text(source_text)
        clone_calls = 0

        def fake_run(cmd, **kwargs):
            nonlocal clone_calls
            if cmd[:2] == ["git", "clone"]:
                clone_calls += 1
                destination = Path(cmd[-1])
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "Utils.geno").write_text(source_text)
            if "rev-parse" in cmd:
                return mock.Mock(returncode=0, stdout=f"{commit}\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            assert install(tmp_path) == ["utils"]

        published = tmp_path / "geno_modules" / "utils"
        migrated = parse_lockfile(tmp_path / "geno.lock").dependencies["utils"]
        assert migrated.content_hash_version == 2
        assert migrated.content_hash == compute_content_hash(published)
        assert clone_calls == 1

    def test_install_existing_dependency_reconciles_to_locked_commit(self, tmp_path):
        """Existing checkouts should be moved back to the locked commit when they drift."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
branch = "main"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        branch="main",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        clone_cmd = commands[0]
        assert clone_cmd[:-1] == [
            "git",
            "clone",
            "--branch",
            "main",
            "--single-branch",
            "https://example.com/utils.git",
        ]
        staged = Path(clone_cmd[-1])
        assert staged.parent.name == "staged"
        assert not any("fetch" in command for command in commands)
        assert [
            "git",
            "-C",
            str(staged),
            "checkout",
            "--force",
            "abcdef1234567890abcdef1234567890abcdef12",
        ] in commands

    def test_install_existing_shallow_dependency_unshallows_before_checkout(
        self, tmp_path
    ):
        """Locked installs replace old shallow metadata with a full staged clone."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
branch = "main"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func foo() -> Int\n  return 1\nend func\n")
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="abcdef1234567890abcdef1234567890abcdef12",
                        branch="main",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == ["utils"]
        clone_cmd = commands[0]
        assert clone_cmd[:-1] == [
            "git",
            "clone",
            "--branch",
            "main",
            "--single-branch",
            "https://example.com/utils.git",
        ]
        staged = Path(clone_cmd[-1])
        assert staged.parent.name == "staged"
        assert not any("fetch" in command for command in commands)
        assert [
            "git",
            "-C",
            str(staged),
            "checkout",
            "--force",
            "abcdef1234567890abcdef1234567890abcdef12",
        ] in commands


class TestPackageTransactions:
    COMMIT = "abcdef1234567890abcdef1234567890abcdef12"

    @staticmethod
    def _write_manifest(root: Path, *names: str) -> None:
        entries = "\n".join(
            f'[dependencies.{name}]\ngit = "https://example.com/{name}.git"'
            for name in names
        )
        (root / "geno.toml").write_text(entries + "\n")

    @staticmethod
    def _clone_with_source(_url, destination, _ref, depth=1) -> None:
        destination.mkdir(parents=True)
        (destination / "Dependency.geno").write_text(
            "func value() -> Int\n  return 2\nend func\n"
        )

    @staticmethod
    def _transaction_dirs(root: Path) -> list[Path]:
        modules = root / "geno_modules"
        return list(modules.glob(".geno-package-txn-*")) if modules.exists() else []

    def _crashed_publication(self, root: Path, *, same_lockfile: bool = False):
        from geno.package_manager import (
            _begin_transaction_journal,
            _create_transaction_root,
            _PreparedCheckout,
        )

        modules_dir = root / "geno_modules"
        destination = modules_dir / "utils"
        destination.mkdir(parents=True)
        old_source = "func value() -> Int\n  return 1\nend func\n"
        new_source = "func value() -> Int\n  return 2\nend func\n"
        (destination / "Dependency.geno").write_text(old_source)

        transaction_root = _create_transaction_root(modules_dir)
        staged = transaction_root / "staged" / "utils"
        staged.mkdir(parents=True)
        (staged / "Dependency.geno").write_text(new_source)
        new_locked = LockedDependency(
            name="utils",
            git="https://example.com/utils.git",
            commit=self.COMMIT,
            content_hash=compute_content_hash(staged),
        )
        old_locked = (
            new_locked
            if same_lockfile
            else LockedDependency(
                name="utils",
                git="https://example.com/utils.git",
                commit="1" * 40,
                content_hash=compute_content_hash(destination),
            )
        )
        save_lockfile(
            Lockfile(dependencies={"utils": old_locked}),
            root / "geno.lock",
        )
        prepared = _PreparedCheckout(
            name="utils",
            destination=destination,
            staged=staged,
            locked=new_locked,
        )
        (transaction_root / "backups").mkdir()
        journal_path, next_lockfile = _begin_transaction_journal(
            [prepared],
            Lockfile(dependencies={"utils": new_locked}),
            root / "geno.lock",
            transaction_root,
        )
        assert prepared.backup is not None
        destination.rename(prepared.backup)
        staged.rename(destination)
        return (
            transaction_root,
            journal_path,
            next_lockfile,
            old_source,
            new_source,
        )

    def test_project_lock_scavenges_prejournal_crash_orphans(self, tmp_path):
        from geno.package_manager import _project_transaction_lock

        modules = tmp_path / "geno_modules"
        transaction = modules / ".geno-package-txn-orphan123"
        (transaction / "staged").mkdir(parents=True)
        (transaction / "staged" / "payload").write_text("orphan")
        marker = tmp_path / ".geno-package-txn-orphan123.lock.next"
        marker.write_text("orphan")

        with _project_transaction_lock(tmp_path, timeout=1):
            assert not transaction.exists()
            assert not marker.exists()

        assert not transaction.exists()
        assert not marker.exists()

    def test_bad_staged_hash_preserves_existing_checkout(self, tmp_path):
        self._write_manifest(tmp_path, "utils")
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        sentinel = dep_dir / "sentinel.txt"
        sentinel.write_text("trusted previous checkout")
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit=self.COMMIT,
                        content_hash="0" * 64,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch("geno.package_manager._git_checkout_commit"),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
        ):
            with pytest.raises(RuntimeError, match=r"does not match geno.lock"):
                install(tmp_path)

        assert sentinel.read_text() == "trusted previous checkout"
        assert not (dep_dir / "Dependency.geno").exists()
        assert self._transaction_dirs(tmp_path) == []

    @pytest.mark.parametrize("existing", [False, True])
    def test_lockfile_save_failure_rolls_back_every_published_checkout(
        self, tmp_path, existing
    ):
        self._write_manifest(tmp_path, "utils")
        dep_dir = tmp_path / "geno_modules" / "utils"
        if existing:
            dep_dir.mkdir(parents=True)
            (dep_dir / "sentinel.txt").write_text("old")

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch(
                "geno.package_manager.save_lockfile",
                side_effect=OSError("lock write failed"),
            ),
        ):
            with pytest.raises(OSError, match="lock write failed"):
                install(tmp_path)

        if existing:
            assert (dep_dir / "sentinel.txt").read_text() == "old"
            assert not (dep_dir / "Dependency.geno").exists()
        else:
            assert not dep_dir.exists()
        assert self._transaction_dirs(tmp_path) == []

    def test_old_checkout_remains_visible_while_clone_is_paused(self, tmp_path):
        import threading

        self._write_manifest(tmp_path, "utils")
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        sentinel = dep_dir / "sentinel.txt"
        sentinel.write_text("old")
        clone_started = threading.Event()
        release_clone = threading.Event()
        errors = []

        def paused_clone(url, destination, ref, depth=1):
            clone_started.set()
            if not release_clone.wait(2):
                raise TimeoutError("test did not release clone")
            self._clone_with_source(url, destination, ref, depth)

        def run_install():
            try:
                install(tmp_path)
            except BaseException as exc:
                errors.append(exc)

        with (
            mock.patch("geno.package_manager._git_clone", side_effect=paused_clone),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
        ):
            thread = threading.Thread(target=run_install)
            thread.start()
            assert clone_started.wait(1)
            assert sentinel.read_text() == "old"
            assert not (dep_dir / "Dependency.geno").exists()
            release_clone.set()
            thread.join(timeout=3)

        assert not thread.is_alive()
        assert errors == []
        assert not sentinel.exists()
        assert (dep_dir / "Dependency.geno").exists()
        assert self._transaction_dirs(tmp_path) == []

    def test_publish_rename_failure_restores_backup(self, tmp_path):
        self._write_manifest(tmp_path, "utils")
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        sentinel = dep_dir / "sentinel.txt"
        sentinel.write_text("old")
        original_rename = Path.rename

        def fail_staged_publish(path, target):
            if path.parent.name == "staged" and Path(target) == dep_dir:
                raise OSError("publish rename failed")
            return original_rename(path, target)

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch.object(Path, "rename", fail_staged_publish),
        ):
            with pytest.raises(OSError, match="publish rename failed"):
                install(tmp_path)

        assert sentinel.read_text() == "old"
        assert not (dep_dir / "Dependency.geno").exists()
        assert self._transaction_dirs(tmp_path) == []

    def test_backup_rename_failure_preserves_existing_checkout(self, tmp_path):
        self._write_manifest(tmp_path, "utils")
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        sentinel = dep_dir / "sentinel.txt"
        sentinel.write_text("old")
        original_rename = Path.rename

        def fail_backup_move(path, target):
            if Path(path) == dep_dir and Path(target).parent.name == "backups":
                raise OSError("backup rename failed")
            return original_rename(path, target)

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch.object(Path, "rename", fail_backup_move),
        ):
            with pytest.raises(OSError, match="backup rename failed"):
                install(tmp_path)

        assert sentinel.read_text() == "old"
        assert not (dep_dir / "Dependency.geno").exists()
        assert self._transaction_dirs(tmp_path) == []

    def test_later_prepare_failure_does_not_publish_earlier_dependency(self, tmp_path):

        self._write_manifest(tmp_path, "alpha", "beta")
        modules = tmp_path / "geno_modules"
        for name in ("alpha", "beta"):
            dep_dir = modules / name
            dep_dir.mkdir(parents=True)
            (dep_dir / "sentinel.txt").write_text(name)

        def clone_then_fail(_url, destination, _ref, depth=1):
            destination.mkdir(parents=True)
            (destination / "partial.txt").write_text("unverified")
            if destination.name == "beta":
                raise RuntimeError("second clone failed")

        with (
            mock.patch("geno.package_manager._git_clone", side_effect=clone_then_fail),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
        ):
            with pytest.raises(RuntimeError, match="second clone failed"):
                install(tmp_path)

        assert (modules / "alpha" / "sentinel.txt").read_text() == "alpha"
        assert (modules / "beta" / "sentinel.txt").read_text() == "beta"
        assert not (modules / "alpha" / "partial.txt").exists()
        assert self._transaction_dirs(tmp_path) == []

    def test_module_reader_waits_for_checkout_publication(self, tmp_path):
        import threading

        self._write_manifest(tmp_path, "utils")
        main_path = tmp_path / "Main.geno"
        main_path.write_text("import Utils\n")
        program = _parse_file(main_path)
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text(
            "func value() -> Int\n  return 1\nend func\n"
        )
        original_rename = Path.rename
        publication_gap = threading.Event()
        release_publication = threading.Event()
        reader_finished = threading.Event()
        install_errors: list[BaseException] = []
        reader_errors: list[BaseException] = []
        resolved_modules: dict[str, str] = {}

        def clone_new_source(_url, destination, _ref, depth=1):
            destination.mkdir(parents=True)
            (destination / "Utils.geno").write_text(
                "func value() -> Int\n  return 2\nend func\n"
            )

        def pause_after_backup(path, target):
            result = original_rename(path, target)
            if Path(path) == dep_dir and Path(target).parent.name == "backups":
                publication_gap.set()
                if not release_publication.wait(3):
                    raise TimeoutError("test did not release publication")
            return result

        def run_install():
            try:
                install(tmp_path)
            except BaseException as exc:
                install_errors.append(exc)

        def run_reader():
            try:
                resolved_modules.update(resolve_modules(main_path, program))
            except BaseException as exc:
                reader_errors.append(exc)
            finally:
                reader_finished.set()

        with (
            mock.patch("geno.package_manager._git_clone", side_effect=clone_new_source),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch.object(Path, "rename", pause_after_backup),
        ):
            install_thread = threading.Thread(target=run_install)
            install_thread.start()
            assert publication_gap.wait(1)
            reader_thread = threading.Thread(target=run_reader)
            reader_thread.start()
            assert not reader_finished.wait(0.1)
            release_publication.set()
            install_thread.join(timeout=3)
            reader_thread.join(timeout=3)

        assert not install_thread.is_alive()
        assert not reader_thread.is_alive()
        assert install_errors == []
        assert reader_errors == []
        assert "return 2" in resolved_modules["Utils"]
        assert self._transaction_dirs(tmp_path) == []

    def test_same_project_thread_lock_serializes_callers(self, tmp_path):
        import threading

        from geno.package_manager import _project_transaction_lock

        entered = threading.Event()

        def contender():
            with _project_transaction_lock(tmp_path, timeout=2):
                entered.set()

        with _project_transaction_lock(tmp_path, timeout=2):
            thread = threading.Thread(target=contender)
            thread.start()
            assert not entered.wait(0.1)
        assert entered.wait(1)
        thread.join(timeout=1)
        assert not thread.is_alive()

    @pytest.mark.parametrize("same_lockfile", [False, True])
    def test_project_lock_recovers_uncommitted_publication(
        self, tmp_path, same_lockfile
    ):
        from geno.package_manager import _project_transaction_lock

        transaction_root, journal_path, next_lockfile, old_source, _new_source = (
            self._crashed_publication(tmp_path, same_lockfile=same_lockfile)
        )
        assert next_lockfile.is_file()

        with _project_transaction_lock(tmp_path, timeout=1):
            pass

        restored = tmp_path / "geno_modules" / "utils" / "Dependency.geno"
        assert restored.read_text() == old_source
        assert not transaction_root.exists()
        assert not journal_path.exists()
        if not same_lockfile:
            assert parse_lockfile(tmp_path / "geno.lock").dependencies[
                "utils"
            ].commit == ("1" * 40)

    @pytest.mark.parametrize("transaction_cleaned", [False, True])
    def test_project_lock_finalizes_committed_publication(
        self, tmp_path, transaction_cleaned
    ):
        from geno.package_manager import _project_transaction_lock

        transaction_root, journal_path, next_lockfile, _old_source, new_source = (
            self._crashed_publication(tmp_path)
        )
        os.replace(next_lockfile, tmp_path / "geno.lock")
        if transaction_cleaned:
            shutil.rmtree(transaction_root)

        with _project_transaction_lock(tmp_path, timeout=1):
            pass

        published = tmp_path / "geno_modules" / "utils" / "Dependency.geno"
        assert published.read_text() == new_source
        assert not transaction_root.exists()
        assert not journal_path.exists()
        locked = parse_lockfile(tmp_path / "geno.lock").dependencies["utils"]
        assert locked.commit == self.COMMIT
        assert locked.content_hash == compute_content_hash(published.parent)

    def test_post_commit_interrupt_finalizes_new_checkout(self, tmp_path):
        self._write_manifest(tmp_path, "utils")
        dependency = tmp_path / "geno_modules" / "utils"
        dependency.mkdir(parents=True)
        (dependency / "Dependency.geno").write_text(
            "func value() -> Int\n  return 1\nend func\n"
        )
        original_replace = os.replace

        def interrupt_after_commit(source, destination):
            result = original_replace(source, destination)
            if str(source).endswith(".lock.next") and Path(destination) == (
                tmp_path / "geno.lock"
            ):
                raise KeyboardInterrupt
            return result

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch("geno.package_manager.os.replace", interrupt_after_commit),
        ):
            with pytest.raises(KeyboardInterrupt):
                install(tmp_path)

        assert "return 2" in (dependency / "Dependency.geno").read_text()
        assert (
            parse_lockfile(tmp_path / "geno.lock").dependencies["utils"].commit
            == self.COMMIT
        )
        assert self._transaction_dirs(tmp_path) == []

    def test_add_post_commit_interrupt_keeps_committed_manifest(self, tmp_path):
        (tmp_path / "geno.toml").write_text("[dependencies]\n")
        original_replace = os.replace

        def interrupt_after_commit(source, destination):
            result = original_replace(source, destination)
            if str(source).endswith(".lock.next") and Path(destination) == (
                tmp_path / "geno.lock"
            ):
                raise KeyboardInterrupt
            return result

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch("geno.package_manager.os.replace", interrupt_after_commit),
        ):
            with pytest.raises(KeyboardInterrupt):
                add(
                    "utils",
                    "https://example.com/utils.git",
                    project_root=tmp_path,
                )

        manifest = parse_manifest(tmp_path / "geno.toml")
        assert manifest.dependencies["utils"].git == ("https://example.com/utils.git")
        locked = parse_lockfile(tmp_path / "geno.lock").dependencies["utils"]
        assert locked.commit == self.COMMIT
        dependency = tmp_path / "geno_modules" / "utils" / "Dependency.geno"
        assert "return 2" in dependency.read_text()
        assert self._transaction_dirs(tmp_path) == []

    def test_pre_journal_failure_removes_root_commit_marker(self, tmp_path):
        self._write_manifest(tmp_path, "utils")

        with (
            mock.patch(
                "geno.package_manager._git_clone", side_effect=self._clone_with_source
            ),
            mock.patch(
                "geno.package_manager._git_head_commit", return_value=self.COMMIT
            ),
            mock.patch(
                "geno.package_manager._write_transaction_journal",
                side_effect=OSError("journal write failed"),
            ),
        ):
            with pytest.raises(OSError, match="journal write failed"):
                install(tmp_path)

        assert list(tmp_path.glob(".geno-package-txn-*.lock.next")) == []
        assert self._transaction_dirs(tmp_path) == []

    def test_interrupted_rollback_is_idempotent(self, tmp_path):
        from geno.package_manager import (
            _project_transaction_lock,
            _recover_project_transaction,
        )

        transaction_root, journal_path, _next_lockfile, old_source, _new_source = (
            self._crashed_publication(tmp_path)
        )
        original_rename = Path.rename

        def interrupt_after_restore(path, target):
            result = original_rename(path, target)
            if Path(path).parent.name == "backups":
                raise KeyboardInterrupt
            return result

        with mock.patch.object(Path, "rename", interrupt_after_restore):
            with pytest.raises(KeyboardInterrupt):
                _recover_project_transaction(tmp_path, journal_path)

        assert journal_path.is_file()
        with _project_transaction_lock(tmp_path, timeout=1):
            pass

        restored = tmp_path / "geno_modules" / "utils" / "Dependency.geno"
        assert restored.read_text() == old_source
        assert not transaction_root.exists()
        assert not journal_path.exists()

    def test_staged_tree_is_fsynced_before_journal_creation(self, tmp_path):
        from geno.package_manager import (
            _begin_transaction_journal,
            _create_transaction_root,
            _fsync_tree,
            _PreparedCheckout,
            _write_transaction_journal,
        )

        modules = tmp_path / "geno_modules"
        transaction_root = _create_transaction_root(modules)
        staged = transaction_root / "staged" / "utils"
        staged.mkdir(parents=True)
        (staged / "Dependency.geno").write_text("new")
        locked = LockedDependency(
            name="utils",
            git="https://example.com/utils.git",
            commit=self.COMMIT,
            content_hash=compute_content_hash(staged),
        )
        prepared = _PreparedCheckout(
            name="utils",
            destination=modules / "utils",
            staged=staged,
            locked=locked,
        )
        events: list[str] = []

        def record_fsync(path):
            events.append("fsync")
            return _fsync_tree(path)

        def record_journal(path, payload):
            events.append("journal")
            return _write_transaction_journal(path, payload)

        with (
            mock.patch("geno.package_manager._fsync_tree", side_effect=record_fsync),
            mock.patch(
                "geno.package_manager._write_transaction_journal",
                side_effect=record_journal,
            ),
        ):
            _begin_transaction_journal(
                [prepared],
                Lockfile(dependencies={"utils": locked}),
                tmp_path / "geno.lock",
                transaction_root,
            )

        assert events == ["fsync", "journal"]

    def test_package_owner_root_prefers_containing_project(self, tmp_path):
        from geno.package_manager import find_package_owner_root

        (tmp_path / "geno.toml").write_text("[dependencies]\n")
        dependency_root = tmp_path / "geno_modules" / "utils"
        dependency_root.mkdir(parents=True)
        (dependency_root / "geno.toml").write_text('entrypoint = "Main"\n')
        source = dependency_root / "Main.geno"
        source.write_text("func main() -> Int\n  return 0\nend func\n")

        assert find_package_owner_root(source) == tmp_path.resolve()

    def test_nested_package_owner_chain_is_acquired_outermost_first(self, tmp_path):
        from geno.package_manager import (
            _package_transaction_locks,
            find_package_lock_roots,
        )

        outer = tmp_path
        first = outer / "geno_modules" / "first"
        second = first / "geno_modules" / "second"
        second.mkdir(parents=True)
        for root in (outer, first, second):
            (root / "geno.toml").write_text("[dependencies]\n")
        source = second / "Main.geno"
        source.write_text("func main() -> Int\n  return 0\nend func\n")
        expected = tuple(root.resolve() for root in (outer, first, second))
        acquired: list[Path] = []

        @contextlib.contextmanager
        def record_lock(root, timeout):
            acquired.append(root)
            yield

        assert find_package_lock_roots(source) == expected
        with mock.patch(
            "geno.package_manager._project_transaction_lock", side_effect=record_lock
        ):
            with _package_transaction_locks(source, timeout=1):
                pass

        assert acquired == list(expected)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX state directory policy")
    def test_project_lock_never_uses_predictable_global_tmp(
        self, tmp_path, monkeypatch
    ):
        import stat

        from geno.package_manager import _secure_lock_directory

        runtime = tmp_path / "runtime"
        runtime.mkdir(mode=0o700)
        runtime.chmod(0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.setattr(
            "geno.package_manager.tempfile.gettempdir",
            lambda: (_ for _ in ()).throw(AssertionError("global temp used")),
        )

        directory = _secure_lock_directory()

        assert directory.is_relative_to(runtime)
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    def test_project_lock_is_reentrant_for_nested_resolvers(self, tmp_path):
        from geno.package_manager import _project_state_paths, _project_transaction_lock

        with _project_transaction_lock(tmp_path, timeout=1):
            with _project_transaction_lock(tmp_path, timeout=0):
                pass

        lock_path, _journal_path = _project_state_paths(tmp_path)
        assert lock_path.is_file()
        assert not (tmp_path / ".geno-package.lock").exists()

    def test_project_lock_releases_thread_state_when_path_setup_fails(
        self, tmp_path, monkeypatch
    ):
        from geno import package_manager

        original_state_paths = package_manager._project_state_paths
        calls = 0

        def flaky_state_paths(root):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient state setup failure")
            return original_state_paths(root)

        monkeypatch.setattr(package_manager, "_project_state_paths", flaky_state_paths)

        with pytest.raises(RuntimeError, match="transient state setup failure"):
            with package_manager._project_transaction_lock(tmp_path, timeout=1):
                pass
        with package_manager._project_transaction_lock(tmp_path, timeout=1):
            pass

        assert calls == 2

    def test_different_project_locks_are_independent(self, tmp_path):
        import threading

        from geno.package_manager import _project_transaction_lock

        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        entered = threading.Event()

        def contender():
            with _project_transaction_lock(second, timeout=1):
                entered.set()

        with _project_transaction_lock(first, timeout=1):
            thread = threading.Thread(target=contender)
            thread.start()
            assert entered.wait(0.5)
        thread.join(timeout=1)
        assert not thread.is_alive()

    def test_project_lock_excludes_other_processes(self, tmp_path):
        import subprocess

        from geno.package_manager import _project_transaction_lock

        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from geno.package_manager import _project_transaction_lock\n"
            "try:\n"
            "    with _project_transaction_lock(Path(sys.argv[1]), timeout=0.2):\n"
            "        print('acquired')\n"
            "except TimeoutError:\n"
            "    print('blocked')\n"
            "    raise SystemExit(3)\n"
        )
        command = [sys.executable, "-c", script, str(tmp_path)]

        with _project_transaction_lock(tmp_path, timeout=1):
            blocked = subprocess.run(
                command, capture_output=True, text=True, timeout=5, check=False
            )
        acquired = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )

        assert blocked.returncode == 3, blocked.stderr
        assert blocked.stdout.strip() == "blocked"
        assert acquired.returncode == 0, acquired.stderr
        assert acquired.stdout.strip() == "acquired"

    def test_project_lock_rejects_symlink(self, tmp_path):
        from geno.package_manager import _project_state_paths, _project_transaction_lock

        lock_path, _journal_path = _project_state_paths(tmp_path)
        lock_path.unlink(missing_ok=True)
        target = lock_path.with_suffix(".target")
        target.write_bytes(b"\0")
        try:
            lock_path.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            target.unlink(missing_ok=True)
            pytest.skip(f"file symlinks are unavailable: {exc}")

        try:
            with pytest.raises(RuntimeError, match="not a regular file"):
                with _project_transaction_lock(tmp_path, timeout=0.1):
                    pass
        finally:
            lock_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)

    def test_project_lock_rejects_hardlink(self, tmp_path):
        from geno.package_manager import _project_state_paths, _project_transaction_lock

        lock_path, _journal_path = _project_state_paths(tmp_path)
        lock_path.unlink(missing_ok=True)
        target = lock_path.with_suffix(".target")
        target.write_bytes(b"\0")
        try:
            os.link(target, lock_path)
        except (OSError, NotImplementedError) as exc:
            target.unlink(missing_ok=True)
            pytest.skip(f"hard links are unavailable: {exc}")

        try:
            with pytest.raises(RuntimeError, match="not a regular file"):
                with _project_transaction_lock(tmp_path, timeout=0.1):
                    pass
        finally:
            lock_path.unlink(missing_ok=True)
            target.unlink(missing_ok=True)

    @pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf")])
    def test_project_lock_rejects_unbounded_timeout(self, tmp_path, timeout):
        from geno.package_manager import _project_transaction_lock

        with pytest.raises(ValueError, match="finite and non-negative"):
            with _project_transaction_lock(tmp_path, timeout=timeout):
                pass


class TestPackageManagerSecurity:
    def test_project_graph_rejects_symlinked_modules_dir(self, tmp_path):
        from geno.project_graph import ProjectGraph, ProjectGraphError

        (tmp_path / "geno.toml").write_text(
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )
        external = tmp_path / "outside"
        (external / "utils").mkdir(parents=True)
        modules = tmp_path / "geno_modules"
        try:
            modules.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        with pytest.raises(ProjectGraphError, match="must not be a symbolic link"):
            ProjectGraph.discover(tmp_path)

    def test_module_resolver_rejects_symlinked_modules_dir(self, tmp_path):
        (tmp_path / "geno.toml").write_text("[dependencies]\n")
        main = tmp_path / "Main.geno"
        main.write_text("import Utils\nfunc main() -> Int\n  return 0\nend func\n")
        external = tmp_path / "outside"
        dependency = external / "utils"
        dependency.mkdir(parents=True)
        (dependency / "Utils.geno").write_text(
            "func value() -> Int\n  return 1\nend func\n"
        )
        modules = tmp_path / "geno_modules"
        try:
            modules.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        with pytest.raises((ModuleResolutionError, RuntimeError), match="symlink"):
            resolve_modules(main, _parse_file(main))

    def test_install_rejects_symlinked_modules_dir(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        external = tmp_path / "external"
        external.mkdir()
        modules_link = tmp_path / "geno_modules"
        try:
            modules_link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        with mock.patch("geno.package_manager.subprocess.run") as mock_run:
            with pytest.raises(RuntimeError, match="Dependency root is a symlink"):
                install(tmp_path)

        mock_run.assert_not_called()

    def test_install_rejects_symlinked_dependency_dir(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        external = tmp_path / "external"
        external.mkdir()
        modules_dir = tmp_path / "geno_modules"
        modules_dir.mkdir()
        dep_link = modules_dir / "utils"
        try:
            dep_link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        with mock.patch("geno.package_manager.subprocess.run") as mock_run:
            with pytest.raises(RuntimeError, match="escapes geno_modules"):
                install(tmp_path)

        mock_run.assert_not_called()

    def test_git_protocol_is_rejected(self):
        with pytest.raises(ValueError, match="Invalid git URL"):
            _validate_git_url("git://example.com/utils.git")

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/user/repo.git",
            "ssh://git@github.com/user/repo.git",
            "git@github.com:user/repo.git",
            "https://127.0.0.1/repo.git",
            "https://localhost/repo.git",
        ],
    )
    def test_git_url_validation_allows_structured_safe_remotes(self, url):
        _validate_git_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/utils.git",
            "git://example.com/utils.git",
            "https://-evil.example/repo.git",
            "https://evil-.example/repo.git",
            "https://evil..example/repo.git",
            "ssh://git@evil_host/repo.git",
            "git@-foo.com:repo.git",
            "git@foo-.com:repo.git",
            "git@host: --upload-pack=evil",
            "git@host:-upload-pack=evil",
            "git@host:",
            "git@host:repo\n.git",
            "https://example.com",
            "https://example.com/repo.git?upload-pack=evil",
            "https://user@example.com/repo.git",
            "https://user:secret@example.com/repo.git",
            "ssh://user:secret@example.com/repo.git",
        ],
    )
    def test_git_url_validation_rejects_unsafe_remotes(self, url):
        with pytest.raises(ValueError, match="Invalid git URL"):
            _validate_git_url(url)

    def test_git_url_validation_redacts_embedded_secret(self):
        secret = "do-not-log-this"
        url = f"https://user:{secret}@example.com/repo.git"

        with pytest.raises(ValueError) as raised:
            _validate_git_url(url)

        assert secret not in str(raised.value)
        assert url not in str(raised.value)

    @pytest.mark.parametrize(
        "ref",
        [
            "",
            "-main",
            "feature branch",
            "feature\\branch",
            "main..next",
            "feature/../main",
            "main@{1}",
            "/main",
            "main/",
            "feature//main",
            "a" * 256,
        ],
    )
    def test_git_ref_validation_rejects_unsafe_refs(self, ref):
        with pytest.raises(ValueError, match="Invalid git branch"):
            _validate_git_ref(ref, "git branch")

    @pytest.mark.parametrize("ref", ["main", "release/v1.0.0", "v0.3.0"])
    def test_git_ref_validation_allows_common_refs(self, ref):
        _validate_git_ref(ref, "git ref")

    @pytest.mark.parametrize("commit", ["a" * 40, "a" * 64])
    def test_git_commit_validation_allows_hex_object_ids(self, commit):
        _validate_git_commit(commit)

    @pytest.mark.parametrize(
        "commit",
        [
            "",
            "deadbee",
            "deadbeef",
            "a" * 39,
            "lockedsha",
            "-deadbee",
            "g" * 40,
            "g" * 64,
        ],
    )
    def test_git_commit_validation_rejects_invalid_object_ids(self, commit):
        with pytest.raises(ValueError, match="Invalid git commit"):
            _validate_git_commit(commit)

    def test_install_accepts_sha256_locked_commit(self, tmp_path):
        commit = "a" * 64
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func x() -> Int\n  return 1\nend func\n")
        content_hash = compute_content_hash(dep_dir)
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit=commit,
                        content_hash=content_hash,
                    )
                }
            ),
            tmp_path / "geno.lock",
        )

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return mock.Mock(returncode=0, stdout=f"{commit}\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            installed = install(tmp_path)

        assert installed == []

    def test_install_rejects_invalid_branch_before_git(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
branch = "-main"
"""
        )

        with mock.patch("geno.package_manager.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Invalid git branch"):
                install(tmp_path)

        mock_run.assert_not_called()

    def test_install_validates_locked_commit_before_checkout(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
branch = "main"
"""
        )
        save_lockfile(
            Lockfile(
                dependencies={
                    "utils": LockedDependency(
                        name="utils",
                        git="https://example.com/utils.git",
                        commit="lockedsha",
                        branch="main",
                    )
                }
            ),
            tmp_path / "geno.lock",
        )
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            with pytest.raises(ValueError, match="Invalid git commit"):
                install(tmp_path)

        assert not any(
            len(cmd) > 4 and cmd[3] == "checkout" and "--force" in cmd
            for cmd in commands
        )


class TestUpdateMocked:
    def test_update_tag_dependency_fetches_and_checks_out_tag(self, tmp_path):
        """Tag-based updates should fetch the tag ref rather than origin/<tag>."""
        (tmp_path / "geno.toml").write_text(
            """
[dependencies.utils]
git = "https://example.com/utils.git"
tag = "v0.3.0"
"""
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func foo() -> Int\n  return 1\nend func\n")

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[:2] == ["git", "clone"]:
                staged = Path(cmd[-1])
                staged.mkdir(parents=True)
                (staged / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 2\nend func\n"
                )
            if "rev-parse" in cmd:
                return mock.Mock(returncode=0, stdout=f"{'a' * 40}\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            updated = update(project_root=tmp_path)

        assert updated == ["utils"]
        clone_cmd = commands[0]
        assert clone_cmd[:-1] == [
            "git",
            "clone",
            "--branch",
            "v0.3.0",
            "--single-branch",
            "--depth",
            "1",
            "https://example.com/utils.git",
        ]
        assert Path(clone_cmd[-1]).name == "utils"
        assert Path(clone_cmd[-1]).parent.name == "staged"
        assert not any("fetch" in command for command in commands)


# =========================================================================
# Package naming convention tests
# =========================================================================


class TestKebabToPascal:
    def test_simple_name(self):
        assert kebab_to_pascal("url") == "Url"

    def test_two_parts(self):
        assert kebab_to_pascal("http-utils") == "HttpUtils"

    def test_three_parts(self):
        assert kebab_to_pascal("my-great-lib") == "MyGreatLib"

    def test_already_pascal(self):
        # Single word starting with uppercase: not kebab, returns as-is
        assert kebab_to_pascal("Utils") == "Utils"

    def test_underscore_returns_unchanged(self):
        assert kebab_to_pascal("bad_name") == "bad_name"

    def test_numeric_returns_unchanged(self):
        assert kebab_to_pascal("lib-2go") == "lib-2go"

    def test_empty_segment_returns_unchanged(self):
        assert kebab_to_pascal("bad--name") == "bad--name"


class TestPascalToKebab:
    def test_simple(self):
        assert pascal_to_kebab("Url") == "url"

    def test_two_parts(self):
        assert pascal_to_kebab("HttpUtils") == "http-utils"

    def test_three_parts(self):
        assert pascal_to_kebab("MyGreatLib") == "my-great-lib"


class TestCanMapToPascal:
    def test_valid_kebab(self):
        assert can_map_to_pascal("http-utils") is True

    def test_simple_alpha(self):
        assert can_map_to_pascal("url") is True

    def test_underscore(self):
        assert can_map_to_pascal("bad_name") is False

    def test_numeric(self):
        assert can_map_to_pascal("123bad") is False

    def test_already_pascal(self):
        assert can_map_to_pascal("HttpUtils") is True


class TestModuleResolverKebabLookup:
    """Test that module resolver finds kebab-case packages via PascalCase imports."""

    def test_find_kebab_package_by_pascal_import(self, tmp_path):
        # Setup: project with geno.toml + geno_modules/http-utils/
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
            '[dependencies.http-utils]\ngit = "https://example.com/repo.git"\n'
        )
        main_geno = tmp_path / "Main.geno"
        main_geno.write_text(
            "import HttpUtils\nfunc main() -> Int\n    return 0\nend func main\n"
        )

        # Create geno_modules/http-utils/ with an entrypoint
        dep_dir = tmp_path / "geno_modules" / "http-utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text('entrypoint = "HttpUtils"\n')
        (dep_dir / "HttpUtils.geno").write_text(
            "func fetch(url: String) -> String\n"
            '    example "test" -> "data"\n'
            '    return "data"\n'
            "end func fetch\n"
        )

        modules = resolve_modules(main_geno, _parse_file(main_geno))
        assert "HttpUtils" in modules


def _parse_file(path):
    """Helper to parse a .geno file."""
    from geno.lexer import Lexer
    from geno.parser import Parser

    source = path.read_text()
    tokens = Lexer(source, str(path)).tokenize()
    return Parser(tokens).parse_program()


class TestAtomicManifestWrites:
    """save_manifest/save_lockfile must not corrupt an existing file if the write
    is interrupted (crash, Ctrl-C, disk full) partway through."""

    def test_save_manifest_preserves_original_on_write_failure(self, tmp_path):
        path = tmp_path / "geno.toml"
        path.write_text('name = "original"\n', encoding="utf-8")

        manifest = Manifest(name="updated", version="2.0", entrypoint="App")
        # Simulate a crash at the atomic rename step.
        with mock.patch("geno.manifest.os.replace", side_effect=OSError("boom")):
            with pytest.raises(OSError):
                save_manifest(manifest, path)

        # Original content intact (never truncated) and no temp file leaked.
        assert path.read_text(encoding="utf-8") == 'name = "original"\n'
        assert [p.name for p in tmp_path.iterdir() if p != path] == []

    def test_save_lockfile_preserves_original_on_write_failure(self, tmp_path):
        path = tmp_path / "geno.lock"
        path.write_text("# original lock\n", encoding="utf-8")

        lockfile = Lockfile(dependencies={})
        with mock.patch("geno.manifest.os.replace", side_effect=OSError("boom")):
            with pytest.raises(OSError):
                save_lockfile(lockfile, path)

        assert path.read_text(encoding="utf-8") == "# original lock\n"
        assert [p.name for p in tmp_path.iterdir() if p != path] == []

    def test_atomic_write_text_round_trips_and_leaves_no_temp(self, tmp_path):
        from geno.manifest import atomic_write_text

        path = tmp_path / "data.txt"
        atomic_write_text(path, "hello\nworld\n")
        assert path.read_text(encoding="utf-8") == "hello\nworld\n"
        atomic_write_text(path, "second\n")  # overwrite works
        assert path.read_text(encoding="utf-8") == "second\n"
        assert [p.name for p in tmp_path.iterdir() if p.name != "data.txt"] == []

    def test_atomic_write_text_preserves_existing_file_mode(self, tmp_path):
        if os.name == "nt":
            pytest.skip("POSIX mode-bit preservation is not meaningful on Windows")

        import stat

        from geno.manifest import atomic_write_text

        path = tmp_path / "data.txt"
        path.write_text("old\n", encoding="utf-8")
        path.chmod(0o640)

        atomic_write_text(path, "new\n")

        assert path.read_text(encoding="utf-8") == "new\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o640

    def test_atomic_write_text_uses_umask_for_new_file(self, tmp_path):
        if os.name == "nt":
            pytest.skip("POSIX umask mode bits are not meaningful on Windows")

        import stat

        from geno.manifest import atomic_write_text

        path = tmp_path / "data.txt"
        old_umask = os.umask(0o027)
        try:
            atomic_write_text(path, "new\n")
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(path.stat().st_mode) == 0o640


class TestGitTimeoutHandling:
    """Git timeouts must surface as a clean error and never poison later installs."""

    def test_run_git_wraps_timeout_expired(self):
        import subprocess

        from geno.package_manager import _run_git

        with mock.patch(
            "geno.package_manager.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=60),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                _run_git(["git", "clone", "https://example.com/x.git", "dest"])

    def test_git_clone_removes_partial_dest_on_failure(self, tmp_path):
        from geno.package_manager import _git_clone

        dest = tmp_path / "dep"

        def _fake_run_git(cmd, **kwargs):
            # Simulate git leaving a partial clone behind before being killed.
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            raise RuntimeError("Git command timed out after 60s: git clone...")

        with mock.patch("geno.package_manager._run_git", side_effect=_fake_run_git):
            with pytest.raises(RuntimeError, match="timed out"):
                _git_clone("https://example.com/dep.git", dest, "main")

        assert not dest.exists()  # partial clone cleaned up, won't poison retries

    def test_git_clone_does_not_remove_preexisting_dest_on_failure(self, tmp_path):
        from geno.package_manager import _git_clone

        dest = tmp_path / "dep"
        dest.mkdir()
        sentinel = dest / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        def _fake_run_git(cmd, **kwargs):
            raise RuntimeError("Git command timed out after 60s: git clone...")

        with mock.patch("geno.package_manager._run_git", side_effect=_fake_run_git):
            with pytest.raises(RuntimeError, match="timed out"):
                _git_clone("https://example.com/dep.git", dest, "main")

        assert sentinel.read_text(encoding="utf-8") == "keep"


class TestAddRollback:
    """M-16: a failed install during `geno add` must roll back the geno.toml
    edit so the project is not left pointing at an uninstalled dependency."""

    def test_add_rolls_back_manifest_on_install_failure(self, tmp_path):
        original = 'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        (tmp_path / "geno.toml").write_text(original)
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )

        def failing_clone(url, dest, ref, depth=1):
            raise RuntimeError("clone failed")

        with mock.patch("geno.package_manager._git_clone", side_effect=failing_clone):
            with pytest.raises(RuntimeError, match="clone failed"):
                add(
                    "utils",
                    "https://example.com/utils.git",
                    project_root=tmp_path,
                )

        # geno.toml must be restored to its original content (no 'utils' dep).
        restored = (tmp_path / "geno.toml").read_text(encoding="utf-8")
        assert restored == original
        assert "utils" not in restored

    def test_add_succeeds_and_persists_manifest(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )

        def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Utils.geno").write_text(
                    "func foo() -> Int\n  return 1\nend func\n"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "rev-parse" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout="abcdef1234567890abcdef1234567890abcdef12\n",
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("geno.package_manager.subprocess.run", side_effect=fake_run):
            add("utils", "https://example.com/utils.git", project_root=tmp_path)

        manifest = (tmp_path / "geno.toml").read_text(encoding="utf-8")
        assert "utils" in manifest
