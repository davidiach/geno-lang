"""
Package Manager
===============

Manages git-based dependencies: install, add, update.
Dependencies are cloned into ``geno_modules/<name>/`` relative to the
project root (the directory containing ``geno.toml``).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn, Optional
from urllib.parse import urlsplit

from .lockfile import (
    LockedDependency,
    Lockfile,
    compute_content_hash,
    parse_lockfile,
    save_lockfile,
)
from .manifest import (
    Dependency,
    Manifest,
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


def install(project_root: Path | None = None) -> list[str]:
    """Install all dependencies declared in geno.toml.

    Returns list of installed dependency names.
    """
    root = project_root or find_project_root()
    manifest = parse_manifest(root / "geno.toml")
    lockfile = parse_lockfile(root / "geno.lock")
    modules_dir = root / "geno_modules"

    installed: list[str] = []

    for name, dep in manifest.dependencies.items():
        if modules_dir.exists() or modules_dir.is_symlink():
            _ensure_modules_dir_safe(modules_dir)
        dep_dir = modules_dir / name
        if dep_dir.exists() or dep_dir.is_symlink():
            _ensure_dependency_dir_contained(dep_dir, modules_dir, name)
        locked = lockfile.dependencies.get(name)
        if _lock_matches_manifest(dep, locked):
            assert locked is not None
            if dep_dir.exists():
                if _git_head_commit(dep_dir) == locked.commit:
                    actual_hash = compute_content_hash(dep_dir)
                    if locked.content_hash and actual_hash == locked.content_hash:
                        continue
                    if not locked.content_hash:
                        logging.getLogger(__name__).warning(
                            "Dependency '%s' is from an old lockfile without "
                            "a content hash. Re-checking out before backfilling.",
                            name,
                        )
                    else:
                        logging.getLogger(__name__).warning(
                            "Dependency '%s' has been modified locally "
                            "(content hash mismatch). Re-checking out.",
                            name,
                        )
                else:
                    _git_fetch_checkout(
                        dep_dir,
                        _locked_ref(locked),
                        is_tag=bool(locked.tag),
                        unshallow=_git_is_shallow(dep_dir),
                    )
            else:
                modules_dir.mkdir(parents=True, exist_ok=True)
                # Lockfile installs must make the pinned commit available even
                # when it is older than the branch head, so avoid shallow clone.
                _git_clone(dep.git, dep_dir, _locked_ref(locked), depth=None)
            _git_checkout_commit(dep_dir, locked.commit)
        else:
            ref = dep.tag or dep.branch
            if dep_dir.exists():
                _git_fetch_checkout(dep_dir, ref, is_tag=bool(dep.tag))
            else:
                modules_dir.mkdir(parents=True, exist_ok=True)
                _git_clone(dep.git, dep_dir, ref)

        commit = _git_head_commit(dep_dir)
        content_hash = compute_content_hash(dep_dir)
        lockfile.dependencies[name] = LockedDependency(
            name=name,
            git=dep.git,
            commit=commit,
            branch=dep.branch,
            tag=dep.tag or "",
            content_hash=content_hash,
        )
        installed.append(name)

    save_lockfile(lockfile, root / "geno.lock")
    return installed


def add(
    name: str,
    git_url: str,
    branch: str = "main",
    tag: str | None = None,
    project_root: Path | None = None,
) -> None:
    """Add a dependency to geno.toml and install it."""
    root = project_root or find_project_root()
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
    try:
        install(root)
    except BaseException:
        if original_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            atomic_write_text(manifest_path, original_manifest)
        raise


def update(name: str | None = None, project_root: Path | None = None) -> list[str]:
    """Update one or all dependencies to latest commits.

    Returns list of updated dependency names.
    """
    root = project_root or find_project_root()
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

    for dep_name, dep in targets.items():
        if modules_dir.exists() or modules_dir.is_symlink():
            _ensure_modules_dir_safe(modules_dir)
        dep_dir = modules_dir / dep_name
        if dep_dir.exists() or dep_dir.is_symlink():
            _ensure_dependency_dir_contained(dep_dir, modules_dir, dep_name)
        ref = dep.tag or dep.branch
        if not dep_dir.exists():
            modules_dir.mkdir(parents=True, exist_ok=True)
            _git_clone(dep.git, dep_dir, ref)
        else:
            _git_fetch_checkout(dep_dir, ref, is_tag=bool(dep.tag))

        commit = _git_head_commit(dep_dir)
        content_hash = compute_content_hash(dep_dir)
        old_commit = (
            lockfile.dependencies[dep_name].commit
            if dep_name in lockfile.dependencies
            else None
        )
        lockfile.dependencies[dep_name] = LockedDependency(
            name=dep_name,
            git=dep.git,
            commit=commit,
            branch=dep.branch,
            tag=dep.tag or "",
            content_hash=content_hash,
        )
        if commit != old_commit:
            updated.append(dep_name)

    save_lockfile(lockfile, root / "geno.lock")
    return updated


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_GIT_REMOTE_FORBIDDEN_RE = re.compile(r"[\000-\037\177\s]")
_GIT_SCP_REMOTE_RE = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>.+)$")
_GIT_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_GIT_REF_MAX_LENGTH = 255
_GIT_REF_FORBIDDEN_RE = re.compile(r"[\000-\037\177\s~^:?*\[]")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{6,40}|[0-9a-fA-F]{64})$")


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
            f"Invalid git URL: {url!r}. "
            "Expected https://, ssh://, or git@host:path format with a valid host."
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
            "Invalid git commit: expected a 6-40 or 64 character hexadecimal object ID"
        )


def _run_git(
    cmd: list[str], timeout: int = 60, **kwargs
) -> subprocess.CompletedProcess:
    """Run a git command with user-friendly error wrapping."""
    try:
        return subprocess.run(  # noqa: S603
            cmd, check=True, capture_output=True, text=True, timeout=timeout, **kwargs
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
