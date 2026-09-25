"""In-memory module identities and conservative filesystem invalidation.

Metadata is a freshness hint, not a content certificate. The compilation-session
layer must eventually bind entries to source bytes and dependency identities.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from compiler.modules import Module


@dataclass(frozen=True)
class _SourceStamp:
    """Named fields avoid positional assumptions about stat results."""

    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


def _source_stamp(path: str) -> _SourceStamp | None:
    """Return the observable file identity, or no evidence on any stat error."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return _SourceStamp(
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
        changed_ns=info.st_ctime_ns,
    )


class ModuleCache:
    """Keep parsed modules in RAM; treat missing freshness evidence as stale."""

    def __init__(self):
        self.modules: dict[str, Module] = {}
        self.loading: set[str] = set()
        self.mtimes: dict[str, float] = {}
        self._stamps: dict[str, _SourceStamp] = {}

    @staticmethod
    def _cache_key(path: str) -> str:
        """Canonical filesystem identity for a module path."""
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def get(self, path: str) -> Module | None:
        """Return a cached object; the loader checks is_stale before calling."""
        return self.modules.get(self._cache_key(path))

    def put(self, path: str, module: Module) -> None:
        """Replace an entry without retaining freshness evidence from its past."""
        key = self._cache_key(path)
        self.modules[key] = module
        self.mtimes.pop(key, None)
        self._stamps.pop(key, None)
        if not module.path or self._cache_key(module.path) != key:
            return
        stamp = _source_stamp(module.path)
        if stamp is not None:
            self._stamps[key] = stamp
            self.mtimes[key] = stamp.modified_ns / 1_000_000_000

    def is_stale(self, file_path: str) -> bool:
        """Reject changed, missing, or uninspectable source identities."""
        key = self._cache_key(file_path)
        if key not in self.modules:
            return True
        previous = self._stamps.get(key)
        if previous is None:
            return True
        current = _source_stamp(file_path)
        return current is None or current != previous

    def invalidate(self, path: str) -> None:
        """Remove a module and all of its freshness evidence."""
        key = self._cache_key(path)
        self.modules.pop(key, None)
        self.mtimes.pop(key, None)
        self._stamps.pop(key, None)

    def clear(self) -> None:
        """Clear cached modules and in-progress import identities."""
        self.modules.clear()
        self.loading.clear()
        self.mtimes.clear()
        self._stamps.clear()

    def is_loading(self, path: str) -> bool:
        return self._cache_key(path) in self.loading

    def start_loading(self, path: str) -> None:
        self.loading.add(self._cache_key(path))

    def finish_loading(self, path: str) -> None:
        self.loading.discard(self._cache_key(path))
