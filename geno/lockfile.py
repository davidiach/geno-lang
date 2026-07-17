"""
Lockfile parsing for geno.lock
==============================

Pins exact git commits for reproducible dependency resolution.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from .manifest import (
    _read_bounded_regular_text,
    atomic_write_text,
    validate_dependency_name,
)

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


@dataclass
class LockedDependency:
    """A dependency pinned to a specific commit."""

    name: str
    git: str
    commit: str
    branch: str = "main"
    tag: str = ""  # semver tag, e.g. "v0.3.0"
    content_hash: str = ""  # SHA-256 of dependency content
    content_hash_version: int = 2


@dataclass
class Lockfile:
    """Parsed representation of geno.lock."""

    dependencies: Dict[str, LockedDependency] = field(default_factory=dict)


_GIT_REF_MAX_LENGTH = 255
_GIT_REF_FORBIDDEN_RE = re.compile(r"[\000-\037\177\s~^:?*\[]")
_GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_CONTENT_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _required_string(raw: dict, key: str, dep_name: str) -> str:
    if key not in raw:
        raise ValueError(f"Lockfile dependency '{dep_name}' must have a '{key}' key")
    value = raw[key]
    if not isinstance(value, str):
        raise ValueError(
            f"Lockfile dependency '{dep_name}' field '{key}' must be a string"
        )
    return value


def _optional_string(raw: dict, key: str, dep_name: str, default: str = "") -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(
            f"Lockfile dependency '{dep_name}' field '{key}' must be a string"
        )
    return value


def _validate_git_ref(ref: str, kind: str, dep_name: str) -> None:
    if not ref:
        raise ValueError(f"Lockfile dependency '{dep_name}' has empty {kind}")
    if len(ref) > _GIT_REF_MAX_LENGTH:
        raise ValueError(f"Lockfile dependency '{dep_name}' {kind} is too long")
    if ref.startswith("-"):
        raise ValueError(f"Lockfile dependency '{dep_name}' {kind} starts with '-'")
    if ref.startswith("/") or ref.endswith("/") or "//" in ref:
        raise ValueError(
            f"Lockfile dependency '{dep_name}' {kind} contains an unsafe separator"
        )
    if "\\" in ref or ".." in ref or "@{" in ref:
        raise ValueError(
            f"Lockfile dependency '{dep_name}' {kind} contains an unsafe sequence"
        )
    if ref.endswith("."):
        raise ValueError(f"Lockfile dependency '{dep_name}' {kind} ends with '.'")
    if _GIT_REF_FORBIDDEN_RE.search(ref):
        raise ValueError(
            f"Lockfile dependency '{dep_name}' {kind} contains unsafe characters"
        )
    for component in ref.split("/"):
        if component in {"", ".", ".."}:
            raise ValueError(
                f"Lockfile dependency '{dep_name}' {kind} contains unsafe components"
            )
        if component.startswith(".") or component.endswith(".lock"):
            raise ValueError(
                f"Lockfile dependency '{dep_name}' {kind} contains unsafe components"
            )


def parse_lockfile(path: Path) -> Lockfile:
    """Parse a geno.lock file. Returns empty Lockfile if file doesn't exist."""
    if not path.exists() and not path.is_symlink():
        return Lockfile()

    if tomllib is None:
        raise RuntimeError(
            "TOML parsing not available. Install tomli for Python < 3.11."
        )

    text = _read_bounded_regular_text(path, label="Geno lockfile")
    raw = tomllib.loads(text)
    raw_deps = raw.get("dependencies", {})
    if not isinstance(raw_deps, dict):
        raise ValueError("Lockfile field 'dependencies' must be a table")

    deps: Dict[str, LockedDependency] = {}
    for name, info in raw_deps.items():
        validate_dependency_name(name)
        if not isinstance(info, dict):
            raise ValueError(f"Lockfile dependency '{name}' must be a table")

        git = _required_string(info, "git", name)
        commit = _required_string(info, "commit", name)
        branch = _optional_string(info, "branch", name, "main")
        tag = _optional_string(info, "tag", name)
        content_hash = _optional_string(info, "content_hash", name)
        content_hash_version = info.get("content_hash_version", 1)
        if (
            not isinstance(content_hash_version, int)
            or isinstance(content_hash_version, bool)
            or content_hash_version not in {1, 2}
        ):
            raise ValueError(
                f"Lockfile dependency '{name}' content_hash_version must be 1 or 2"
            )
        if "content_hash_version" in info and not content_hash:
            raise ValueError(
                f"Lockfile dependency '{name}' has a hash version without a content_hash"
            )

        if not _GIT_COMMIT_RE.fullmatch(commit):
            raise ValueError(
                f"Invalid git commit in lockfile dependency '{name}': "
                "expected 40 or 64 hex characters"
            )
        _validate_git_ref(tag or branch, "git ref", name)
        if content_hash and not _CONTENT_HASH_RE.fullmatch(content_hash):
            raise ValueError(
                f"Lockfile dependency '{name}' content_hash must be a SHA-256 hex digest"
            )
        content_hash = content_hash.casefold()

        deps[name] = LockedDependency(
            name=name,
            git=git,
            commit=commit,
            branch=branch,
            tag=tag,
            content_hash=content_hash,
            content_hash_version=content_hash_version,
        )

    return Lockfile(dependencies=deps)


def _toml_escape(value: str) -> str:
    """Escape a string for use in a TOML quoted value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _serialize_toml_key(key: str) -> str:
    """Serialize a TOML key, quoting it when required."""
    if _BARE_TOML_KEY_RE.fullmatch(key):
        return key
    return f'"{_toml_escape(key)}"'


def save_lockfile(lockfile: Lockfile, path: Path) -> None:
    """Write a Lockfile to geno.lock (TOML format)."""
    lines: list[str] = []

    for name, dep in lockfile.dependencies.items():
        lines.append(f"[dependencies.{_serialize_toml_key(name)}]")
        lines.append(f'git = "{_toml_escape(dep.git)}"')
        lines.append(f'commit = "{_toml_escape(dep.commit)}"')
        lines.append(f'branch = "{_toml_escape(dep.branch)}"')
        if dep.tag:
            lines.append(f'tag = "{_toml_escape(dep.tag)}"')
        if dep.content_hash:
            lines.append(f'content_hash = "{_toml_escape(dep.content_hash)}"')
            lines.append(f"content_hash_version = {dep.content_hash_version}")
        lines.append("")

    atomic_write_text(path, "\n".join(lines) + "\n")


_CONTENT_HASH_IGNORED_DIRS = frozenset({".git"})


def _hash_path(path: str) -> bytes:
    return path.encode("utf-8", errors="surrogateescape")


def _update_content_hash_field(hasher: Any, value: bytes) -> None:
    """Hash one unambiguous length-prefixed binary field."""
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _compute_content_hashes(directory: Path) -> tuple[str, str]:
    """Compute the current and legacy SHA-256 dependency tree hashes."""
    h = hashlib.sha256()
    legacy = hashlib.sha256()
    h.update(b"geno-dependency-tree-v2\0")
    if directory.is_symlink():
        raise RuntimeError(
            f"Dependency content root must not be a symlink: {directory}"
        )
    root = directory.resolve(strict=True)

    for current_root, dir_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        ignored_symlink_dirs: list[str] = []
        kept_dirs: list[str] = []
        for name in dir_names:
            path = current / name
            if name in _CONTENT_HASH_IGNORED_DIRS:
                continue
            if path.is_symlink():
                ignored_symlink_dirs.append(name)
                continue
            kept_dirs.append(name)
        dir_names[:] = sorted(kept_dirs)

        for name in sorted(file_names + ignored_symlink_dirs):
            if name in _CONTENT_HASH_IGNORED_DIRS:
                continue
            path = current / name
            mode = path.lstat().st_mode
            if not (stat.S_ISLNK(mode) or stat.S_ISREG(mode)):
                raise RuntimeError(
                    f"Unsupported filesystem entry in dependency tree: {path}"
                )

            relative_path = path.relative_to(root).as_posix()

            _update_content_hash_field(h, b"entry")
            _update_content_hash_field(h, _hash_path(relative_path))
            legacy.update(_hash_path(relative_path))
            legacy.update(b"\0")

            if stat.S_ISLNK(mode):
                try:
                    resolved_target = path.resolve(strict=True)
                    relative_target = resolved_target.relative_to(root)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise RuntimeError(
                        f"Dependency symlink escapes its checkout or is dangling: {path}"
                    ) from exc
                if ".git" in relative_target.parts or not (
                    resolved_target.is_file() or resolved_target.is_dir()
                ):
                    raise RuntimeError(
                        f"Dependency symlink target is unsafe or unhashed: {path}"
                    )
                _update_content_hash_field(h, b"symlink")
                _update_content_hash_field(h, _hash_path(os.readlink(path)))
                legacy.update(b"symlink\0")
                legacy.update(_hash_path(os.readlink(path)))
                legacy.update(b"\0")
                continue

            if stat.S_ISREG(mode):
                if path.stat(follow_symlinks=False).st_nlink != 1:
                    raise RuntimeError(
                        f"Hard-linked files are not supported in dependency trees: {path}"
                    )
                _update_content_hash_field(h, b"file")
                _update_content_hash_field(
                    h,
                    b"executable=1" if stat.S_IMODE(mode) & 0o111 else b"executable=0",
                )
                contents = path.read_bytes()
                _update_content_hash_field(h, contents)
                legacy.update(b"file\0")
                legacy.update(b"executable=")
                legacy.update(b"1" if stat.S_IMODE(mode) & 0o111 else b"0")
                legacy.update(b"\0")
                legacy.update(contents)
                legacy.update(b"\0")
    return h.hexdigest(), legacy.hexdigest()


def compute_content_hash(directory: Path) -> str:
    """Compute the current SHA-256 hash of dependency tree contents."""
    current, _legacy = _compute_content_hashes(directory)
    return current


def compute_legacy_content_hash(directory: Path) -> str:
    """Compute the pre-v2 hash solely to migrate an existing lockfile."""
    _current, legacy = _compute_content_hashes(directory)
    return legacy
