"""
Package Manager
===============

Manages git-based dependencies: install, add, update.
Dependencies are cloned into ``geno_modules/<name>/`` relative to the
project root (the directory containing ``geno.toml``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, NoReturn, cast
from urllib.parse import urlsplit

from .lockfile import (
    LockedDependency,
    Lockfile,
    compute_content_hash,
    compute_legacy_content_hash,
    parse_lockfile,
    save_lockfile,
)
from .manifest import (
    Dependency,
    atomic_write_text,
    parse_manifest,
    save_manifest,
    validate_dependency_name,
)


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* looking for geno.toml. Raise if not found."""
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / "geno.toml").exists():
            return parent
    raise FileNotFoundError("No geno.toml found in current directory or any parent")


def find_package_lock_roots(start: Path) -> tuple[Path, ...]:
    """Return every project lock that can publish content containing *start*."""
    current = Path(os.path.abspath(start))
    nearest: Path | None = None
    owners: set[Path] = set()
    for parent in [current, *current.parents]:
        if not (parent / "geno.toml").exists():
            continue
        resolved_parent = parent.resolve()
        if nearest is None:
            nearest = resolved_parent
        modules_root = Path(os.path.abspath(parent / "geno_modules"))
        try:
            current.relative_to(modules_root)
        except ValueError:
            continue
        owners.add(resolved_parent)
    if nearest is not None:
        owners.add(nearest)
    return tuple(
        sorted(owners, key=lambda path: (len(path.parts), os.path.normcase(str(path))))
    )


def find_package_owner_root(start: Path) -> Path | None:
    """Return the outermost project whose publication can replace *start*."""
    roots = find_package_lock_roots(start)
    return roots[0] if roots else None


_PACKAGE_LOCK_DIRECTORY_PREFIX = "geno-package-locks-v1"
_PACKAGE_LOCK_TIMEOUT_SECONDS = 30.0
_PROJECT_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_PROJECT_THREAD_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCK_DEPTHS = threading.local()
_PUBLICATION_OUTCOMES = threading.local()
_LOGGER = logging.getLogger(__name__)
_TRANSACTION_JOURNAL_VERSION = 2
_TRANSACTION_NAME_RE = re.compile(r"^\.geno-package-txn-[A-Za-z0-9_-]+$")
_TRANSACTION_LOCKFILE_SUFFIX = ".lock.next"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _thread_lock_for(root: Path) -> threading.RLock:
    with _PROJECT_THREAD_LOCKS_GUARD:
        return _PROJECT_THREAD_LOCKS.setdefault(root, threading.RLock())


def _current_uid() -> int | None:
    """Return the current POSIX uid without assuming it exists on Windows."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return None
    return int(cast(Any, getuid)())


def _secure_lock_directory() -> Path:
    """Return a private external directory for package locks and journals."""
    uid = _current_uid()
    if os.name == "nt":
        directory = Path(tempfile.gettempdir()) / (
            f"{_PACKAGE_LOCK_DIRECTORY_PREFIX}-user"
        )
    else:
        runtime_value = os.environ.get("XDG_RUNTIME_DIR")
        runtime_root = Path(runtime_value) if runtime_value else None
        runtime_secure = False
        if runtime_root is not None and runtime_root.is_absolute():
            try:
                runtime_info = runtime_root.lstat()
                runtime_secure = (
                    stat.S_ISDIR(runtime_info.st_mode)
                    and not runtime_root.is_symlink()
                    and (uid is None or runtime_info.st_uid == uid)
                    and stat.S_IMODE(runtime_info.st_mode) & 0o077 == 0
                )
            except OSError:
                pass
        if runtime_secure:
            assert runtime_root is not None
            state_root = runtime_root
        else:
            state_root = Path.home() / ".local" / "state"
        directory = state_root / "geno" / _PACKAGE_LOCK_DIRECTORY_PREFIX
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Could not create secure package lock directory: {directory}"
        ) from exc
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"Package lock directory is not secure: {directory}")
    info = directory.stat()
    if uid is not None:
        if info.st_uid != uid:
            raise RuntimeError(
                f"Package lock directory is owned by another user: {directory}"
            )
        os.chmod(directory, 0o700)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise RuntimeError(f"Package lock directory is not private: {directory}")
    return directory.resolve()


def _project_state_paths(root: Path) -> tuple[Path, Path]:
    normalized = os.path.normcase(str(root.resolve()))
    key = hashlib.sha256(
        normalized.encode("utf-8", errors="surrogateescape")
    ).hexdigest()
    state_dir = _secure_lock_directory()
    return state_dir / f"{key}.lock", state_dir / f"{key}.journal"


def _fsync_directory(directory: Path) -> None:
    """Persist directory entry changes where the platform supports it."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_state_file(path: Path, fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
        raise RuntimeError(f"Package state path is not a regular file: {path}")
    uid = _current_uid()
    if uid is not None and info.st_uid != uid:
        raise RuntimeError(f"Package state file is owned by another user: {path}")


def _open_project_lock(path: Path) -> int:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Package lock path is not a regular file: {path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        _validate_state_file(path, fd)
        if os.name == "nt" and os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        if os.name != "nt":
            cast(Any, os).fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _try_lock_project_file(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt_api = cast(Any, msvcrt)
        msvcrt_api.locking(fd, msvcrt_api.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl_api = cast(Any, fcntl)
        fcntl_api.flock(fd, fcntl_api.LOCK_EX | fcntl_api.LOCK_NB)


def _unlock_project_file(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt_api = cast(Any, msvcrt)
        msvcrt_api.locking(fd, msvcrt_api.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl_api = cast(Any, fcntl)
        fcntl_api.flock(fd, fcntl_api.LOCK_UN)


@contextlib.contextmanager
def _project_transaction_lock(
    project_root: Path, timeout: float = _PACKAGE_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Serialize package mutations and dependency readers for one project."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Package lock timeout must be finite and non-negative")
    root = project_root.resolve()
    thread_lock = _thread_lock_for(root)
    if not thread_lock.acquire(timeout=timeout):
        raise TimeoutError(f"Timed out waiting for package lock in {root}")

    depths = getattr(_PROJECT_LOCK_DEPTHS, "depths", None)
    if depths is None:
        depths = {}
        _PROJECT_LOCK_DEPTHS.depths = depths
    depth = depths.get(root, 0)
    if depth:
        depths[root] = depth + 1
        try:
            yield
        finally:
            remaining = depths[root] - 1
            if remaining:
                depths[root] = remaining
            else:
                depths.pop(root, None)
            thread_lock.release()
        return

    depths[root] = 1
    fd: int | None = None
    file_locked = False
    try:
        lock_path, journal_path = _project_state_paths(root)
        fd = _open_project_lock(lock_path)
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock_project_file(fd)
                file_locked = True
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for package lock in {root}"
                    ) from exc
                time.sleep(0.05)
        _recover_project_transaction(root, journal_path)
        if not journal_path.exists() and not journal_path.is_symlink():
            _scavenge_orphan_transactions(root)
        yield
    finally:
        depths.pop(root, None)
        if fd is not None:
            if file_locked:
                try:
                    _unlock_project_file(fd)
                except OSError:
                    pass
            os.close(fd)
        thread_lock.release()


@contextlib.contextmanager
def _package_transaction_locks(
    start: Path, timeout: float = _PACKAGE_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Acquire the complete publication-owner chain in canonical order."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Package lock timeout must be finite and non-negative")
    deadline = time.monotonic() + timeout
    acquired: list[Path] = []
    with contextlib.ExitStack() as stack:
        while True:
            roots = find_package_lock_roots(start)
            if tuple(acquired) != roots[: len(acquired)]:
                raise RuntimeError("Package owner chain changed while acquiring locks")
            if len(acquired) == len(roots):
                break
            root = roots[len(acquired)]
            remaining = max(0.0, deadline - time.monotonic())
            stack.enter_context(_project_transaction_lock(root, timeout=remaining))
            acquired.append(root)
        yield


@dataclass
class _PreparedCheckout:
    name: str
    destination: Path
    staged: Path
    locked: LockedDependency
    backup: Path | None = None
    had_destination: bool = False


@dataclass
class _PublicationOutcome:
    committed: bool = False


def _remove_checkout(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _cleanup_transaction(transaction_root: Path) -> None:
    try:
        shutil.rmtree(transaction_root)
    except OSError as exc:
        _LOGGER.warning(
            "Could not clean package transaction directory %s: %s",
            transaction_root,
            exc,
        )


def _create_transaction_root(modules_dir: Path) -> Path:
    if modules_dir.exists() or modules_dir.is_symlink():
        _ensure_modules_dir_safe(modules_dir)
    modules_dir.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(prefix=".geno-package-txn-", dir=modules_dir)
    ).resolve()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_tree(root: Path) -> None:
    """Durably flush a staged checkout before its lockfile can commit."""
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"Staged dependency tree is unsafe: {root}")
    directories: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        dir_names[:] = [
            name
            for name in dir_names
            if name != ".git" and not (current / name).is_symlink()
        ]
        directories.append(current)
        for name in file_names:
            if name == ".git":
                continue
            path = current / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if not stat.S_ISREG(mode):
                raise RuntimeError(f"Unsupported staged filesystem entry: {path}")
            flags = os.O_RDWR if os.name == "nt" else os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(path, flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in reversed(directories):
        _fsync_directory(directory)


def _remove_transaction_journal(journal_path: Path) -> None:
    journal_path.unlink(missing_ok=True)
    _fsync_directory(journal_path.parent)


def _write_transaction_journal(journal_path: Path, payload: dict[str, Any]) -> None:
    if journal_path.exists() or journal_path.is_symlink():
        raise RuntimeError(
            f"Package transaction journal already exists: {journal_path}"
        )
    atomic_write_text(
        journal_path,
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    )
    os.chmod(journal_path, 0o600)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(journal_path, flags)
    try:
        _validate_state_file(journal_path, fd)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(journal_path.parent)


def _begin_transaction_journal(
    prepared: list[_PreparedCheckout],
    lockfile: Lockfile,
    lockfile_path: Path,
    transaction_root: Path,
) -> tuple[Path, Path]:
    """Durably describe a transaction before the first checkout rename."""
    checkouts: list[dict[str, Any]] = []
    for index, checkout in enumerate(prepared):
        _fsync_tree(checkout.staged)
        staged_hash = compute_content_hash(checkout.staged)
        if staged_hash != checkout.locked.content_hash:
            raise RuntimeError(
                f"Dependency '{checkout.name}' changed while preparing publication"
            )
        checkout.had_destination = (
            checkout.destination.exists() or checkout.destination.is_symlink()
        )
        old_content_hash = None
        if checkout.had_destination:
            old_content_hash = compute_content_hash(checkout.destination)
        checkout.backup = (
            transaction_root / "backups" / f"{index}-{checkout.name}"
            if checkout.had_destination
            else None
        )
        checkouts.append(
            {
                "content_hash": checkout.locked.content_hash,
                "had_destination": checkout.had_destination,
                "old_content_hash": old_content_hash,
                "index": index,
                "name": checkout.name,
            }
        )

    next_lockfile = lockfile_path.parent / (
        transaction_root.name + _TRANSACTION_LOCKFILE_SUFFIX
    )
    save_lockfile(lockfile, next_lockfile)
    _fsync_directory(lockfile_path.parent)
    _lock_path, journal_path = _project_state_paths(lockfile_path.parent)
    payload = {
        "checkouts": checkouts,
        "lockfile_sha256": _hash_file(next_lockfile),
        "transaction": transaction_root.name,
        "version": _TRANSACTION_JOURNAL_VERSION,
    }
    _write_transaction_journal(journal_path, payload)
    return journal_path, next_lockfile


def _read_transaction_journal(journal_path: Path) -> dict[str, Any]:
    if journal_path.is_symlink() or not journal_path.is_file():
        raise RuntimeError(
            f"Package transaction journal is not regular: {journal_path}"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(journal_path, flags)
    try:
        _validate_state_file(journal_path, fd)
        info = os.fstat(fd)
        if info.st_size > 128 * 1024:
            raise RuntimeError("Package transaction journal is too large")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Package transaction journal is invalid") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict):
        raise RuntimeError("Package transaction journal is invalid")
    return payload


def _validated_journal_records(
    payload: dict[str, Any],
) -> tuple[str, str, list[tuple[int, str, bool, str, str | None]]]:
    if type(payload.get("version")) is not int or payload["version"] != 2:
        raise RuntimeError("Unsupported package transaction journal version")
    transaction = payload.get("transaction")
    lockfile_sha256 = payload.get("lockfile_sha256")
    raw_checkouts = payload.get("checkouts")
    if not isinstance(transaction, str) or not _TRANSACTION_NAME_RE.fullmatch(
        transaction
    ):
        raise RuntimeError("Package transaction journal has an invalid directory")
    if not isinstance(lockfile_sha256, str) or not _SHA256_RE.fullmatch(
        lockfile_sha256
    ):
        raise RuntimeError("Package transaction journal has an invalid lock digest")
    if not isinstance(raw_checkouts, list) or len(raw_checkouts) > 1024:
        raise RuntimeError("Package transaction journal has invalid checkouts")

    records: list[tuple[int, str, bool, str, str | None]] = []
    names: set[str] = set()
    for expected_index, item in enumerate(raw_checkouts):
        if not isinstance(item, dict):
            raise RuntimeError("Package transaction journal has invalid checkouts")
        index = item.get("index")
        name = item.get("name")
        had_destination = item.get("had_destination")
        content_hash = item.get("content_hash")
        old_content_hash = item.get("old_content_hash")
        if type(index) is not int or index != expected_index:
            raise RuntimeError("Package transaction journal has invalid checkout order")
        if not isinstance(name, str):
            raise RuntimeError(
                "Package transaction journal has invalid dependency name"
            )
        try:
            validate_dependency_name(name)
        except ValueError as exc:
            raise RuntimeError(
                "Package transaction journal has invalid dependency name"
            ) from exc
        if name in names or type(had_destination) is not bool:
            raise RuntimeError("Package transaction journal has invalid checkouts")
        if not isinstance(content_hash, str) or not _SHA256_RE.fullmatch(content_hash):
            raise RuntimeError("Package transaction journal has invalid content hash")
        if had_destination:
            if not isinstance(old_content_hash, str) or not _SHA256_RE.fullmatch(
                old_content_hash
            ):
                raise RuntimeError("Package transaction journal has invalid old hash")
        elif old_content_hash is not None:
            raise RuntimeError("Package transaction journal has unexpected old hash")
        names.add(name)
        records.append((index, name, had_destination, content_hash, old_content_hash))
    return transaction, lockfile_sha256, records


def _safe_transaction_root(root: Path, transaction_name: str) -> Path | None:
    modules_dir = root / "geno_modules"
    _ensure_modules_dir_safe(modules_dir)
    transaction_root = modules_dir / transaction_name
    if transaction_root.is_symlink():
        raise RuntimeError("Package recovery data is unsafe")
    if not transaction_root.exists():
        return None
    if not transaction_root.is_dir():
        raise RuntimeError("Package recovery data is unsafe")
    resolved = transaction_root.resolve()
    if not resolved.is_relative_to(modules_dir.resolve()):
        raise RuntimeError("Package recovery data escapes geno_modules")
    return resolved


def _scavenge_orphan_transactions(root: Path) -> None:
    """Remove crash leftovers while the external project lock is held."""
    uid = _current_uid()
    changed_root = False
    for candidate in root.iterdir():
        if not candidate.name.endswith(_TRANSACTION_LOCKFILE_SUFFIX):
            continue
        transaction_name = candidate.name[: -len(_TRANSACTION_LOCKFILE_SUFFIX)]
        if not _TRANSACTION_NAME_RE.fullmatch(transaction_name):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"Orphan package marker is unsafe: {candidate}")
        info = candidate.stat()
        if not stat.S_ISREG(info.st_mode) or (uid is not None and info.st_uid != uid):
            raise RuntimeError(f"Orphan package marker is unsafe: {candidate}")
        candidate.unlink()
        changed_root = True

    modules_dir = root / "geno_modules"
    if modules_dir.exists() or modules_dir.is_symlink():
        _ensure_modules_dir_safe(modules_dir)
        changed_modules = False
        for candidate in modules_dir.iterdir():
            if not _TRANSACTION_NAME_RE.fullmatch(candidate.name):
                continue
            if candidate.is_symlink() or not candidate.is_dir():
                raise RuntimeError(f"Orphan package transaction is unsafe: {candidate}")
            info = candidate.stat()
            if uid is not None and info.st_uid != uid:
                raise RuntimeError(f"Orphan package transaction is unsafe: {candidate}")
            if not candidate.resolve().is_relative_to(modules_dir.resolve()):
                raise RuntimeError(f"Orphan package transaction escapes: {candidate}")
            _cleanup_transaction(candidate)
            changed_modules = True
        if changed_modules:
            _fsync_directory(modules_dir)
    if changed_root:
        _fsync_directory(root)


def _recover_project_transaction(root: Path, journal_path: Path) -> bool:
    """Roll back an uncommitted publish or finalize a committed one."""
    if not journal_path.exists() and not journal_path.is_symlink():
        return False
    payload = _read_transaction_journal(journal_path)
    transaction_name, target_digest, records = _validated_journal_records(payload)
    transaction_root = _safe_transaction_root(root, transaction_name)
    next_lockfile = root / (transaction_name + _TRANSACTION_LOCKFILE_SUFFIX)
    lockfile_path = root / "geno.lock"
    target_is_current = (
        lockfile_path.is_file()
        and not lockfile_path.is_symlink()
        and _hash_file(lockfile_path) == target_digest
    )

    # os.replace(next_lockfile, lockfile_path) is the commit point. Checking the
    # staged file as well as the target digest distinguishes a same-lockfile
    # reinstall that crashed before the replace from a committed transaction.
    next_exists = next_lockfile.exists() or next_lockfile.is_symlink()
    if next_exists:
        if next_lockfile.is_symlink() or not next_lockfile.is_file():
            raise RuntimeError("Package transaction lockfile is unsafe")
        if _hash_file(next_lockfile) != target_digest:
            raise RuntimeError("Package transaction lockfile is corrupt")

    commit_marker_consumed = not next_exists
    committed = target_is_current and commit_marker_consumed
    if commit_marker_consumed and not committed:
        raise RuntimeError("Package transaction commit state is inconsistent")
    modules_dir = root / "geno_modules"

    if committed:
        for _index, name, _had_destination, content_hash, _old_hash in records:
            destination = modules_dir / name
            _ensure_dependency_dir_contained(destination, modules_dir, name)
            if (
                not destination.is_dir()
                or compute_content_hash(destination) != content_hash
            ):
                raise RuntimeError(
                    f"Committed package transaction is incomplete for '{name}'"
                )
        _fsync_directory(modules_dir)
        _fsync_directory(root)
        _remove_transaction_journal(journal_path)
        if transaction_root is not None:
            _cleanup_transaction(transaction_root)
        return True

    if transaction_root is None:
        raise RuntimeError("Package recovery data is missing")

    staged_dir = transaction_root / "staged"
    backups_dir = transaction_root / "backups"
    if staged_dir.is_symlink() or backups_dir.is_symlink():
        raise RuntimeError("Package recovery directories are unsafe")
    for index, name, had_destination, content_hash, old_content_hash in reversed(
        records
    ):
        destination = modules_dir / name
        staged = staged_dir / name
        backup = backups_dir / f"{index}-{name}"
        for candidate in (destination, staged, backup):
            if candidate.is_symlink():
                raise RuntimeError("Package recovery path is a symlink")
        destination_exists = destination.exists()
        staged_exists = staged.exists()
        backup_exists = backup.exists()
        if staged_exists and compute_content_hash(staged) != content_hash:
            raise RuntimeError(
                f"Package transaction staged data is corrupt for '{name}'"
            )
        if had_destination:
            assert old_content_hash is not None
            if backup_exists:
                if compute_content_hash(backup) != old_content_hash:
                    raise RuntimeError(
                        f"Package transaction backup is corrupt for '{name}'"
                    )
                if destination_exists and staged_exists:
                    raise RuntimeError(
                        f"Package transaction has ambiguous dependency '{name}'"
                    )
                if destination_exists:
                    _remove_checkout(destination)
                backup.rename(destination)
            elif not destination_exists:
                raise RuntimeError(
                    f"Package transaction cannot restore dependency '{name}'"
                )
            elif compute_content_hash(destination) != old_content_hash:
                raise RuntimeError(
                    f"Package transaction restored data is corrupt for '{name}'"
                )
        elif backup_exists:
            raise RuntimeError(
                f"Package transaction has unexpected backup for '{name}'"
            )
        elif destination_exists:
            if staged_exists:
                raise RuntimeError(
                    f"Package transaction has ambiguous dependency '{name}'"
                )
            _remove_checkout(destination)
    _fsync_directory(modules_dir)
    _remove_transaction_journal(journal_path)
    try:
        next_lockfile.unlink(missing_ok=True)
        _fsync_directory(root)
    except OSError as exc:
        _LOGGER.warning(
            "Could not clean package transaction marker %s: %s",
            next_lockfile,
            exc,
        )
    _cleanup_transaction(transaction_root)
    return False


def _prepare_dependency_checkout(
    *,
    name: str,
    dep: Dependency,
    destination: Path,
    transaction_root: Path,
    locked: LockedDependency | None,
) -> _PreparedCheckout:
    ref = dep.tag or dep.branch
    _validate_git_url(dep.git)
    _validate_git_ref(ref, "git tag" if dep.tag else "git branch")
    staged_parent = transaction_root / "staged"
    staged_parent.mkdir(parents=True, exist_ok=True)
    staged = staged_parent / name

    if _lock_matches_manifest(dep, locked):
        assert locked is not None
        _validate_git_commit(locked.commit)
        _git_clone(dep.git, staged, _locked_ref(locked), depth=None)
        _git_checkout_commit(staged, locked.commit)
    else:
        _git_clone(dep.git, staged, ref)

    commit = _git_head_commit(staged)
    _validate_git_commit(commit)
    if locked is not None and _lock_matches_manifest(dep, locked):
        if commit.casefold() != locked.commit.casefold():
            raise RuntimeError(
                f"Dependency '{name}' checked-out commit does not match geno.lock"
            )

    content_hash = compute_content_hash(staged)
    expected_content_hash = content_hash
    if locked is not None and locked.content_hash_version == 1:
        expected_content_hash = compute_legacy_content_hash(staged)
    if (
        locked is not None
        and _lock_matches_manifest(dep, locked)
        and locked.content_hash
        and expected_content_hash != locked.content_hash
    ):
        raise RuntimeError(f"Dependency '{name}' content does not match geno.lock")

    return _PreparedCheckout(
        name=name,
        destination=destination,
        staged=staged,
        locked=LockedDependency(
            name=name,
            git=dep.git,
            commit=commit,
            branch=dep.branch,
            tag=dep.tag or "",
            content_hash=content_hash,
            content_hash_version=2,
        ),
    )


def _publish_transaction(
    prepared: list[_PreparedCheckout],
    lockfile: Lockfile,
    lockfile_path: Path,
    transaction_root: Path,
    publication_outcome: _PublicationOutcome | None = None,
) -> None:
    for checkout in prepared:
        lockfile.dependencies[checkout.name] = checkout.locked

    backups = transaction_root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    _lock_path, journal_path = _project_state_paths(lockfile_path.parent)
    next_lockfile = lockfile_path.parent / (
        transaction_root.name + _TRANSACTION_LOCKFILE_SUFFIX
    )
    try:
        returned_journal_path, returned_next_lockfile = _begin_transaction_journal(
            prepared,
            lockfile,
            lockfile_path,
            transaction_root,
        )
        assert returned_journal_path == journal_path
        assert returned_next_lockfile == next_lockfile
        for index, checkout in enumerate(prepared):
            if checkout.had_destination:
                assert checkout.backup == backups / f"{index}-{checkout.name}"
                checkout.destination.rename(checkout.backup)
            checkout.staged.rename(checkout.destination)
        _fsync_directory(backups)
        _fsync_directory(transaction_root / "staged")
        _fsync_directory(lockfile_path.parent / "geno_modules")
        os.replace(next_lockfile, lockfile_path)
        _fsync_directory(lockfile_path.parent)
        if publication_outcome is not None:
            publication_outcome.committed = True
    except BaseException:
        if journal_path.exists() or journal_path.is_symlink():
            try:
                committed = _recover_project_transaction(
                    lockfile_path.parent, journal_path
                )
                if publication_outcome is not None and committed:
                    publication_outcome.committed = True
            except BaseException as recovery_error:
                raise RuntimeError(
                    "Package transaction failed and automatic recovery could not "
                    f"complete; recovery data remains in {transaction_root}"
                ) from recovery_error
        else:
            try:
                next_lockfile.unlink(missing_ok=True)
                _fsync_directory(lockfile_path.parent)
            finally:
                _cleanup_transaction(transaction_root)
        raise
    _remove_transaction_journal(journal_path)
    _cleanup_transaction(transaction_root)


def install(project_root: Path | None = None) -> list[str]:
    """Install all dependencies declared in geno.toml.

    Returns list of installed dependency names.
    """
    root = (project_root or find_project_root()).resolve()
    with _package_transaction_locks(root):
        return _install_locked(root)


def _install_locked(root: Path) -> list[str]:
    manifest = parse_manifest(root / "geno.toml")
    lockfile = parse_lockfile(root / "geno.lock")
    modules_dir = root / "geno_modules"

    installed: list[str] = []
    prepared: list[_PreparedCheckout] = []
    transaction_root: Path | None = None

    try:
        if modules_dir.exists() or modules_dir.is_symlink():
            _ensure_modules_dir_safe(modules_dir)
        for name, dep in manifest.dependencies.items():
            ref = dep.tag or dep.branch
            _validate_git_url(dep.git)
            _validate_git_ref(ref, "git tag" if dep.tag else "git branch")
            dep_dir = modules_dir / name
            if dep_dir.exists() or dep_dir.is_symlink():
                _ensure_dependency_dir_contained(dep_dir, modules_dir, name)
            locked = lockfile.dependencies.get(name)
            if _lock_matches_manifest(dep, locked):
                assert locked is not None
                _validate_git_commit(locked.commit)
                if dep_dir.exists() and locked.content_hash:
                    # A v1 match is ambiguous by construction. Always stage the
                    # exact locked commit and migrate it to v2 instead of trusting
                    # the installed tree indefinitely.
                    if (
                        locked.content_hash_version == 2
                        and _locked_content_hash_matches(dep_dir, locked)
                    ):
                        try:
                            installed_commit = _git_head_commit(dep_dir)
                        except RuntimeError:
                            _LOGGER.warning(
                                "Dependency '%s' has unverifiable Git metadata. "
                                "Replacing it.",
                                name,
                            )
                        else:
                            if installed_commit.casefold() == locked.commit.casefold():
                                continue
                            _LOGGER.warning(
                                "Dependency '%s' is not at the locked commit. "
                                "Replacing it.",
                                name,
                            )
                    else:
                        _LOGGER.warning(
                            "Dependency '%s' has an outdated or mismatched "
                            "content hash. Replacing it.",
                            name,
                        )
                elif dep_dir.exists():
                    _LOGGER.warning(
                        "Dependency '%s' is from an old lockfile without "
                        "a content hash. Replacing it before backfilling.",
                        name,
                    )
            if transaction_root is None:
                transaction_root = _create_transaction_root(modules_dir)
            prepared.append(
                _prepare_dependency_checkout(
                    name=name,
                    dep=dep,
                    destination=dep_dir,
                    transaction_root=transaction_root,
                    locked=locked,
                )
            )
            installed.append(name)
    except BaseException:
        if transaction_root is not None:
            _cleanup_transaction(transaction_root)
        raise

    if transaction_root is None:
        save_lockfile(lockfile, root / "geno.lock")
    else:
        publication_outcome = getattr(_PUBLICATION_OUTCOMES, "outcome", None)
        _publish_transaction(
            prepared,
            lockfile,
            root / "geno.lock",
            transaction_root,
            publication_outcome,
        )
    return installed


def add(
    name: str,
    git_url: str,
    branch: str = "main",
    tag: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Add a dependency to geno.toml and install it."""
    root = (project_root or find_project_root()).resolve()
    with _package_transaction_locks(root):
        _add_locked(name, git_url, branch, tag, root)


def _add_locked(
    name: str, git_url: str, branch: str, tag: str | None, root: Path
) -> None:
    validate_dependency_name(name)
    _validate_git_url(git_url)
    _validate_git_ref(branch, "git branch")
    if tag is not None:
        _validate_git_ref(tag, "git tag")
        if branch != "main":
            raise ValueError("Dependency cannot specify both a branch and a tag")
    manifest_path = root / "geno.toml"
    manifest = parse_manifest(manifest_path)
    # Snapshot the manifest so a failed install can be rolled back — otherwise
    # geno.toml is left pointing at an uninstalled dependency and every
    # subsequent command fails (M-16).
    original_manifest = (
        manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None
    )

    manifest.dependencies[name] = Dependency(
        name=name,
        git=git_url,
        branch=branch,
        tag=tag,
    )
    save_manifest(manifest, manifest_path)

    publication_outcome = _PublicationOutcome()
    previous_outcome = getattr(_PUBLICATION_OUTCOMES, "outcome", None)
    _PUBLICATION_OUTCOMES.outcome = publication_outcome
    try:
        try:
            _install_locked(root)
        except BaseException:
            if not publication_outcome.committed:
                if original_manifest is None:
                    manifest_path.unlink(missing_ok=True)
                else:
                    atomic_write_text(manifest_path, original_manifest)
            raise
    finally:
        if previous_outcome is None:
            try:
                del _PUBLICATION_OUTCOMES.outcome
            except AttributeError:
                pass
        else:
            _PUBLICATION_OUTCOMES.outcome = previous_outcome


def update(name: str | None = None, project_root: Path | None = None) -> list[str]:
    """Update one or all dependencies to latest commits.

    Returns list of updated dependency names.
    """
    root = (project_root or find_project_root()).resolve()
    with _package_transaction_locks(root):
        return _update_locked(name, root)


def _update_locked(name: str | None, root: Path) -> list[str]:
    manifest = parse_manifest(root / "geno.toml")
    lockfile = parse_lockfile(root / "geno.lock")
    modules_dir = root / "geno_modules"

    if name:
        if name not in manifest.dependencies:
            raise KeyError(f"Dependency '{name}' not found in geno.toml")
        targets = {name: manifest.dependencies[name]}
    else:
        targets = manifest.dependencies

    updated: list[str] = []
    prepared: list[_PreparedCheckout] = []
    transaction_root: Path | None = None

    try:
        if modules_dir.exists() or modules_dir.is_symlink():
            _ensure_modules_dir_safe(modules_dir)
        for dep_name, dep in targets.items():
            ref = dep.tag or dep.branch
            _validate_git_url(dep.git)
            _validate_git_ref(ref, "git tag" if dep.tag else "git branch")
            dep_dir = modules_dir / dep_name
            if dep_dir.exists() or dep_dir.is_symlink():
                _ensure_dependency_dir_contained(dep_dir, modules_dir, dep_name)
            if transaction_root is None:
                transaction_root = _create_transaction_root(modules_dir)
            checkout = _prepare_dependency_checkout(
                name=dep_name,
                dep=dep,
                destination=dep_dir,
                transaction_root=transaction_root,
                locked=None,
            )
            prepared.append(checkout)
            old_commit = (
                lockfile.dependencies[dep_name].commit
                if dep_name in lockfile.dependencies
                else None
            )
            if old_commit is None or checkout.locked.commit != old_commit:
                updated.append(dep_name)
    except BaseException:
        if transaction_root is not None:
            _cleanup_transaction(transaction_root)
        raise

    if transaction_root is None:
        save_lockfile(lockfile, root / "geno.lock")
    else:
        _publish_transaction(prepared, lockfile, root / "geno.lock", transaction_root)
    return updated


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_GIT_REMOTE_FORBIDDEN_RE = re.compile(r"[\000-\037\177\s]")
_GIT_SCP_REMOTE_RE = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>.+)$")
_GIT_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_GIT_REF_MAX_LENGTH = 255
_GIT_REF_FORBIDDEN_RE = re.compile(r"[\000-\037\177\s~^:?*\[]")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def _ensure_modules_dir_safe(modules_dir: Path) -> None:
    """Refuse dependency roots that redirect installs outside the project."""
    if modules_dir.is_symlink():
        raise RuntimeError(f"Dependency root is a symlink: {modules_dir}")
    if modules_dir.exists() and not modules_dir.is_dir():
        raise RuntimeError(f"Dependency root is not a directory: {modules_dir}")


def _ensure_dependency_dir_contained(
    dep_dir: Path, modules_dir: Path, name: str
) -> None:
    """Refuse dependency paths that escape geno_modules via symlinks."""
    _ensure_modules_dir_safe(modules_dir)
    modules_root = modules_dir.resolve()
    resolved = dep_dir.resolve()
    if dep_dir.is_symlink() or not resolved.is_relative_to(modules_root):
        raise RuntimeError(
            f"Dependency '{name}' path escapes geno_modules or is a symlink: {dep_dir}"
        )


def _validate_git_url(url: str) -> None:
    """Reject dependency remotes that are unsafe to pass to git."""

    def invalid() -> NoReturn:
        raise ValueError(
            "Invalid git URL. Expected a credential-free https://, ssh://, "
            "or git@host:path remote with a valid host."
        )

    def validate_host(host: str | None) -> None:
        if host is None or not host:
            invalid()
        if host.startswith("-") or host.endswith("-"):
            invalid()
        if not _GIT_HOST_RE.fullmatch(host):
            invalid()
        for label in host.split("."):
            if not label or label.startswith("-") or label.endswith("-"):
                invalid()

    def validate_path(path: str) -> None:
        if not path or path in {"/", "."}:
            invalid()
        if path.startswith("-") or path.startswith("/-"):
            invalid()

    if not url or _GIT_REMOTE_FORBIDDEN_RE.search(url):
        invalid()

    scp_match = _GIT_SCP_REMOTE_RE.fullmatch(url)
    if scp_match:
        validate_host(scp_match.group("host"))
        validate_path(scp_match.group("path"))
        return

    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "ssh"}:
        invalid()
    if parsed.password is not None:
        invalid()
    if parsed.scheme == "https" and parsed.username is not None:
        invalid()
    if parsed.query or parsed.fragment:
        invalid()
    validate_host(parsed.hostname)
    validate_path(parsed.path)


def _validate_git_ref(ref: str, kind: str = "git ref") -> None:
    """Reject branch/tag refs that are unsafe to pass to git commands."""
    if not ref:
        raise ValueError(f"Invalid {kind}: ref must not be empty")
    if len(ref) > _GIT_REF_MAX_LENGTH:
        raise ValueError(
            f"Invalid {kind}: ref exceeds {_GIT_REF_MAX_LENGTH} characters"
        )
    if ref.startswith("-"):
        raise ValueError(f"Invalid {kind}: ref must not start with '-'")
    if ref.startswith("/") or ref.endswith("/") or "//" in ref:
        raise ValueError(f"Invalid {kind}: ref contains an unsafe path separator")
    if "\\" in ref or ".." in ref or "@{" in ref:
        raise ValueError(f"Invalid {kind}: ref contains an unsafe sequence")
    if ref.endswith("."):
        raise ValueError(f"Invalid {kind}: ref must not end with '.'")
    if _GIT_REF_FORBIDDEN_RE.search(ref):
        raise ValueError(f"Invalid {kind}: ref contains unsafe characters")
    for component in ref.split("/"):
        if component in {"", ".", ".."}:
            raise ValueError(f"Invalid {kind}: ref contains unsafe path components")
        if component.startswith(".") or component.endswith(".lock"):
            raise ValueError(f"Invalid {kind}: ref contains unsafe path components")


def _validate_git_commit(commit: str) -> None:
    """Reject lockfile commits that are not hex object IDs."""
    if not _GIT_COMMIT_RE.fullmatch(commit):
        raise ValueError(
            "Invalid git commit: expected a 40 or 64 character hexadecimal object ID"
        )


def _run_git(
    cmd: list[str], timeout: int = 60, **kwargs
) -> subprocess.CompletedProcess:
    """Run a git command with user-friendly error wrapping."""
    caller_env = kwargs.pop("env", None)
    git_env = os.environ.copy()
    if caller_env is not None:
        git_env.update(caller_env)
    # Command-scope configuration overrides repository-local core.hooksPath,
    # including hooks copied through a global init.templateDir.
    git_env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=git_env,
            **kwargs,
        )
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(
            f"Git command failed: {' '.join(cmd[:3])}...\n{detail}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Git command timed out after {timeout}s: {' '.join(cmd[:3])}... "
            "(the remote may be unreachable or too slow)"
        ) from e


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _lock_matches_manifest(dep: Dependency, locked: LockedDependency | None) -> bool:
    """Return whether a lockfile entry still matches the manifest dependency."""
    return locked is not None and (
        locked.git == dep.git
        and locked.branch == dep.branch
        and locked.tag == (dep.tag or "")
    )


def _locked_content_hash_matches(directory: Path, locked: LockedDependency) -> bool:
    if locked.content_hash_version == 1:
        return bool(compute_legacy_content_hash(directory) == locked.content_hash)
    return bool(compute_content_hash(directory) == locked.content_hash)


def _locked_ref(locked: LockedDependency) -> str:
    """Return the git ref stored in the lockfile entry."""
    return str(locked.tag or locked.branch)


def _git_clone(url: str, dest: Path, ref: str, depth: int | None = 1) -> None:
    _validate_git_url(url)
    _validate_git_ref(ref, "git ref")
    dest_preexisted = dest.exists() or dest.is_symlink()
    cmd = [
        "git",
        "clone",
        "--branch",
        ref,
        "--single-branch",
    ]
    if depth is not None:
        cmd.extend(["--depth", str(depth)])
    cmd.extend([url, str(dest)])
    try:
        _run_git(cmd)
    except BaseException:
        # A clone killed by a timeout (or interrupted) can leave a partial dest
        # directory that poisons subsequent installs; remove it so the next
        # attempt starts from a clean slate.
        if not dest_preexisted and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise


def _git_fetch_checkout(
    repo: Path, ref: str, is_tag: bool = False, unshallow: bool = False
) -> None:
    _validate_git_ref(ref, "git tag" if is_tag else "git branch")
    fetch_cmd = ["git", "-C", str(repo), "fetch"]
    if unshallow:
        fetch_cmd.append("--unshallow")
    fetch_cmd.append("origin")
    checkout_target = f"origin/{ref}"
    if is_tag:
        fetch_cmd.extend(["tag", ref])
        checkout_target = f"refs/tags/{ref}"
    else:
        fetch_cmd.append(ref)
    _run_git(fetch_cmd)
    _run_git(["git", "-C", str(repo), "checkout", checkout_target], timeout=30)


def _git_checkout_commit(repo: Path, commit: str) -> None:
    _validate_git_commit(commit)
    _run_git(["git", "-C", str(repo), "checkout", "--force", commit], timeout=30)
    _run_git(["git", "-C", str(repo), "clean", "-ffdx"], timeout=30)


def _git_is_shallow(repo: Path) -> bool:
    result = _run_git(
        ["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"],
        timeout=10,
    )
    return str(result.stdout).strip() == "true"


def _git_head_commit(repo: Path) -> str:
    result = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        timeout=10,
    )
    return str(result.stdout).strip()
