"""Read-only Git objects must not strand Windows package transactions (#190)."""

import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from geno import package_manager
from geno.lockfile import (
    LockedDependency,
    Lockfile,
    compute_content_hash,
    save_lockfile,
)


@pytest.fixture
def readonly_objects(tmp_path):
    def create(root):
        path = root / ".git" / "objects" / "pack" / "fixture.pack"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"git object fixture")
        path.chmod(stat.S_IREAD)
        return path

    yield create

    # Keep pytest's own temporary-directory cleanup independent of this fix.
    for path in tmp_path.rglob("fixture.pack"):
        if path.resolve().is_relative_to(tmp_path.resolve()):
            path.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only deletion semantics")
@pytest.mark.parametrize("operation", ["transaction", "checkout"])
def test_cleanup_removes_windows_readonly_git_object(
    tmp_path, readonly_objects, operation
):
    transaction = tmp_path / "geno_modules" / ".geno-package-txn-test"
    checkout = transaction / "backups" / "0-lib"
    readonly_objects(checkout)
    if operation == "transaction":
        package_manager._cleanup_transaction(transaction)
        assert not transaction.exists()
    else:
        package_manager._remove_checkout(checkout)
        assert not checkout.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only deletion semantics")
@pytest.mark.parametrize("operation", ["install", "update"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_replacement_cleans_readonly_objects(
    tmp_path, readonly_objects, monkeypatch, operation, rollback
):
    (tmp_path / "geno.toml").write_text(
        '[dependencies.lib]\ngit = "https://example.com/new.git"\n'
    )
    checkout = tmp_path / "geno_modules" / "lib"
    checkout.mkdir(parents=True)
    (checkout / "Lib.geno").write_text("// old source\n")
    readonly_objects(checkout)
    save_lockfile(
        Lockfile(
            dependencies={
                "lib": LockedDependency(
                    name="lib",
                    git="https://example.com/old.git",
                    commit="1" * 40,
                    content_hash=compute_content_hash(checkout),
                )
            }
        ),
        tmp_path / "geno.lock",
    )
    old_lock = (tmp_path / "geno.lock").read_bytes()

    def clone(_url, destination, _ref, depth=1):
        destination.mkdir(parents=True)
        (destination / "Lib.geno").write_text("// new source\n")
        readonly_objects(destination)

    monkeypatch.setattr(package_manager, "_git_clone", clone)
    monkeypatch.setattr(package_manager, "_git_head_commit", lambda _path: "2" * 40)
    if rollback:
        original_replace = os.replace

        def fail_lock_publish(path, destination):
            if Path(destination) == tmp_path / "geno.lock":
                raise OSError("lock publication failed")
            return original_replace(path, destination)

        monkeypatch.setattr(package_manager.os, "replace", fail_lock_publish)
        with pytest.raises(OSError, match="lock publication failed"):
            getattr(package_manager, operation)(project_root=tmp_path)
        assert (tmp_path / "geno.lock").read_bytes() == old_lock
    else:
        getattr(package_manager, operation)(project_root=tmp_path)
        assert (tmp_path / "geno.lock").read_bytes() != old_lock

    expected = "// old source\n" if rollback else "// new source\n"
    assert (checkout / "Lib.geno").read_text() == expected
    assert list((tmp_path / "geno_modules").glob(".geno-package-txn-*")) == []
    assert (
        not (checkout / ".git/objects/pack/fixture.pack").stat().st_mode & stat.S_IWRITE
    )


def _simulate_windows_unlink_failure(monkeypatch, failed_path, error, retry=os.unlink):
    # Exercise callback policy on every CI platform, while the integration
    # tests above use real Windows filesystem failures.
    monkeypatch.setattr(
        package_manager,
        "os",
        SimpleNamespace(name="nt", unlink=retry, remove=os.remove, chmod=os.chmod),
    )

    def rmtree(_root, *, onerror):
        onerror(retry, str(failed_path), (type(error), error, None))

    monkeypatch.setattr(package_manager.shutil, "rmtree", rmtree)


def _readonly_error():
    error = PermissionError(errno.EACCES, "read-only file")
    error.winerror = 5
    return error


def test_readonly_retry_unlinks_contained_file(tmp_path, readonly_objects, monkeypatch):
    root = tmp_path / "checkout"
    obj = readonly_objects(root)
    _simulate_windows_unlink_failure(monkeypatch, obj, _readonly_error())
    package_manager._remove_checkout(root)
    assert not obj.exists()


@pytest.mark.parametrize("failure", ["outside", "writable", "hardlink", "unrelated"])
def test_readonly_retry_does_not_broaden_cleanup(
    tmp_path, readonly_objects, monkeypatch, failure
):
    root = tmp_path / "checkout"
    root.mkdir()
    obj = readonly_objects(tmp_path / "outside" if failure == "outside" else root)
    if failure == "writable":
        obj.chmod(stat.S_IREAD | stat.S_IWRITE)
    elif failure == "hardlink":
        os.link(obj, tmp_path / "external-link.pack")
    error = (
        OSError(errno.EIO, "unrelated failure")
        if failure == "unrelated"
        else _readonly_error()
    )
    before = obj.stat().st_mode
    _simulate_windows_unlink_failure(monkeypatch, obj, error)
    with pytest.raises(type(error)) as caught:
        package_manager._remove_checkout(root)
    assert caught.value is error
    assert obj.exists()
    assert obj.stat().st_mode == before
    if failure == "hardlink":
        obj.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_cleanup_warning_preserves_unrelated_failure(tmp_path, monkeypatch, caplog):
    root = tmp_path / "transaction"
    root.mkdir()

    def fail(*_args, **_kwargs):
        raise PermissionError("directory ACL denies deletion")

    monkeypatch.setattr(package_manager.shutil, "rmtree", fail)
    package_manager._cleanup_transaction(root)
    assert root.exists()
    assert "directory ACL denies deletion" in caplog.text


def test_readonly_retry_does_not_follow_file_symlink(
    tmp_path, readonly_objects, monkeypatch
):
    root = tmp_path / "checkout"
    root.mkdir()
    target = readonly_objects(tmp_path / "outside")
    link = root / "link.pack"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("File symlink creation is unavailable")
    error = _readonly_error()
    before = target.stat().st_mode
    _simulate_windows_unlink_failure(monkeypatch, link, error)
    with pytest.raises(PermissionError) as caught:
        package_manager._remove_checkout(root)
    assert caught.value is error
    assert target.exists()
    assert target.stat().st_mode == before


def test_failed_readonly_retry_restores_file_permissions(
    tmp_path, readonly_objects, monkeypatch
):
    root = tmp_path / "checkout"
    obj = readonly_objects(root)
    before = obj.stat().st_mode
    retry_error = PermissionError("deletion still denied")

    def fail_retry(_filename):
        raise retry_error

    _simulate_windows_unlink_failure(monkeypatch, obj, _readonly_error(), fail_retry)
    with pytest.raises(PermissionError) as caught:
        package_manager._remove_checkout(root)
    assert caught.value is retry_error
    assert obj.exists()
    assert obj.stat().st_mode == before


@pytest.mark.parametrize("depth", [1, None], ids=["shallow", "full"])
def test_local_git_rewrite_clones_private_objects_and_cleans_transaction(
    tmp_path, monkeypatch, depth
):
    git_executable = shutil.which("git")
    if git_executable is None:
        pytest.skip("Git is required for the local clone cleanup regression")
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*arguments):
        assert git_executable is not None
        subprocess.run(
            [
                git_executable,
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "user.name=Package Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *map(str, arguments),
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        source = tmp_path / "source"
        git("init", "--initial-branch=main", source)
        (source / "Lib.geno").write_text("// package fixture\n")
        git("-C", source, "add", "Lib.geno")
        git("-C", source, "commit", "-m", "fixture")
        git("-C", source, "gc", "--prune=now")
        objects = source / ".git" / "objects" / "pack"
        originals = {
            path.name: (path.read_bytes(), path.stat().st_mode)
            for path in objects.iterdir()
        }
        assert originals
        # A plain local-path rewrite exercises Git's hardlink optimization;
        # file:// uses the transport path and would miss this regression.
        git(
            "config",
            "--file",
            config,
            f"url.{source.as_posix()}.insteadOf",
            "https://fixture.invalid/lib.git",
        )
        transaction = tmp_path / "geno_modules" / ".geno-package-txn-local"
        checkout = transaction / "backups" / "0-lib"
        checkout.parent.mkdir(parents=True)
        package_manager._git_clone(
            "https://fixture.invalid/lib.git", checkout, "main", depth=depth
        )
        copies = list((checkout / ".git" / "objects" / "pack").iterdir())
        assert copies
        assert all(path.stat().st_nlink == 1 for path in copies)
        # Git's local copy mode can leave objects writable. Also exercise
        # read-only objects produced by later fetch/repack operations.
        for path in copies:
            path.chmod(stat.S_IREAD)
        if os.name == "nt":
            assert all(not path.stat().st_mode & stat.S_IWRITE for path in copies)

        package_manager._cleanup_transaction(transaction)
        assert not transaction.exists()
        assert {
            path.name: (path.read_bytes(), path.stat().st_mode)
            for path in objects.iterdir()
        } == originals
    finally:
        # Source Git objects remain read-only by design. All fixture files,
        # including any links left by a failing implementation, are owned here.
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert path.resolve().is_relative_to(tmp_path.resolve())
                path.chmod(path.stat().st_mode | stat.S_IWRITE)
