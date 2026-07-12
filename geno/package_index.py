"""
Geno Package Index
==================

Static JSON index of curated packages.  Supports ``geno search`` and
name-only resolution for ``geno add``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from .manifest import validate_dependency_name
from .package_manager import _validate_git_ref
from .target_profile import VALID_TARGETS

_INDEX_CACHE: List[Dict] | None = None

_GIT_URL_RE = re.compile(r"^(https?://|ssh://|git@[\w.\-]+:)")


def _is_valid_package_entry(pkg: object) -> bool:
    if not isinstance(pkg, dict):
        return False
    required_strings = ("name", "description", "git", "latest_version", "tag")
    if any(not isinstance(pkg.get(field), str) for field in required_strings):
        return False
    targets = pkg.get("targets")
    if not isinstance(targets, list) or any(
        not isinstance(target, str) for target in targets
    ):
        return False
    if any(target not in VALID_TARGETS for target in targets):
        return False
    try:
        validate_dependency_name(pkg["name"])
        _validate_git_ref(pkg["tag"], "git tag")
    except ValueError:
        return False
    return bool(_GIT_URL_RE.match(pkg["git"]))


def _normalize_index(data: object) -> List[Dict]:
    """Return only valid package entries from parsed index data."""
    if not isinstance(data, dict):
        return []

    packages = data.get("packages", [])
    if not isinstance(packages, list):
        return []

    return [pkg for pkg in packages if _is_valid_package_entry(pkg)]


def _load_index() -> List[Dict]:
    """Load the bundled packages.json index (cached after first call)."""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    index_path = Path(__file__).parent / "packages.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _INDEX_CACHE = []
        return _INDEX_CACHE
    _INDEX_CACHE = _normalize_index(data)
    return _INDEX_CACHE


def search(query: str) -> List[Dict]:
    """Search the package index. Matches name and description (case-insensitive)."""
    packages = _load_index()
    q = query.lower()
    results = []
    for pkg in packages:
        name = pkg.get("name", "").lower()
        desc = pkg.get("description", "").lower()
        if q in name or q in desc:
            results.append(pkg)
    return results


def resolve(name: str) -> Dict | None:
    """Resolve a package name to its index entry, or None if not found."""
    packages = _load_index()
    for pkg in packages:
        if pkg.get("name") == name:
            return pkg
    return None
