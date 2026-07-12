"""Cache primitives for the Geno language server."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any, Hashable


class BoundedDict(dict[Any, Any]):
    """Dict with a max-size cap; evicts least-recently-used entries on insert."""

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        self._maxsize = maxsize
        self._order: list[Any] = []
        self._lock = threading.RLock()

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            value = super().__getitem__(key)
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
            return value

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self:
                try:
                    self._order.remove(key)
                except ValueError:
                    pass
            super().__setitem__(key, value)
            self._order.append(key)
            while len(self) > self._maxsize and self._order:
                oldest = self._order.pop(0)
                super().pop(oldest, None)

    def __delitem__(self, key: Any) -> None:
        with self._lock:
            super().__delitem__(key)
            try:
                self._order.remove(key)
            except ValueError:
                pass

    def pop(self, key: Any, *args: Any) -> Any:
        with self._lock:
            result = super().pop(key, *args)
            try:
                self._order.remove(key)
            except ValueError:
                pass
            return result

    def clear(self) -> None:
        with self._lock:
            super().clear()
            self._order.clear()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self:
                return self[key]
            return default


def project_view_cache_key(
    project_paths: frozenset[str],
    open_document_paths: Iterable[str],
    open_document_versions: dict[str, int],
) -> tuple[frozenset[str], tuple[tuple[str, int], ...]]:
    """Build a cache key from project membership and open-document revisions."""
    revisions = tuple(
        sorted(
            (path, open_document_versions.get(path, 0))
            for path in open_document_paths
            if path in project_paths
        )
    )
    return project_paths, revisions


def project_view_keys_for_path(
    cache_keys: Iterable[tuple[frozenset[str], Any]],
    path_str: str,
) -> list[tuple[frozenset[str], Any]]:
    """Return project-view cache keys whose project membership includes a path."""
    return [cache_key for cache_key in cache_keys if path_str in cache_key[0]]


def symbol_table_keys_for_path(
    cache_keys: Iterable[Hashable],
    path_str: str,
) -> list[Hashable]:
    """Return symbol-table cache keys whose embedded project paths include a path."""
    stale_keys: list[Hashable] = []
    for cache_key in cache_keys:
        if not isinstance(cache_key, tuple) or len(cache_key) < 2:
            continue
        nested_key = cache_key[1]
        if not isinstance(nested_key, tuple) or not nested_key:
            continue
        project_paths = nested_key[0]
        if path_str in project_paths:
            stale_keys.append(cache_key)
    return stale_keys
