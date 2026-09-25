"""In-memory module identities and conservative dependency invalidation.

Filesystem metadata remains a freshness hint, not a content certificate.
Dependency generations prevent a rebuilt child from blessing an old parent.
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


@dataclass(frozen=True)
class _Dependency:
    """Preserve both the import path and the exact cached child generation."""

    path: str
    key: str
    generation: int | None


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
    """Keep parsed modules in RAM and validate their dependency closure."""

    def __init__(self):
        self.modules: dict[str, Module] = {}
        self.loading: set[str] = set()
        self.mtimes: dict[str, float] = {}
        self._stamps: dict[str, _SourceStamp] = {}
        self._dependencies: dict[str, tuple[_Dependency, ...]] = {}
        self._generations: dict[str, int] = {}
        self._next_generation = 0

    @staticmethod
    def _cache_key(path: str) -> str:
        """Canonical filesystem identity for a module path."""
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def get(self, path: str) -> Module | None:
        """Return a cached object; the loader checks is_stale before calling."""
        return self.modules.get(self._cache_key(path))

    def put(
        self, path: str, module: Module, *, dependencies: tuple[str, ...] = ()
    ) -> None:
        """Replace an entry and record the exact generations it incorporated."""
        key = self._cache_key(path)
        self.invalidate(path)
        self.modules[key] = module
        self._next_generation += 1
        self._generations[key] = self._next_generation
        self._dependencies[key] = tuple(
            self._dependency(dependency) for dependency in dict.fromkeys(dependencies)
        )
        if not module.path or self._cache_key(module.path) != key:
            return
        stamp = _source_stamp(module.path)
        if stamp is not None:
            self._stamps[key] = stamp
            self.mtimes[key] = stamp.modified_ns / 1_000_000_000

    def _dependency(self, path: str) -> _Dependency:
        key = self._cache_key(path)
        return _Dependency(os.path.abspath(path), key, self._generations.get(key))

    def _dependency_changed(self, dependency: _Dependency) -> bool:
        """A missing, rebuilt, or redirected import invalidates its parent."""
        return (
            dependency.generation is None
            or self._cache_key(dependency.path) != dependency.key
            or self._generations.get(dependency.key) != dependency.generation
        )

    def is_stale(self, file_path: str) -> bool:
        """Check each reachable module once, without rereading source bytes."""
        pending = [self._cache_key(file_path)]
        visited: set[str] = set()
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            previous = self._stamps.get(key)
            if key not in self.modules or previous is None:
                return True
            if _source_stamp(key) != previous:
                return True
            for dependency in self._dependencies.get(key, ()):
                if self._dependency_changed(dependency):
                    return True
                pending.append(dependency.key)
        return False

    def invalidate(self, path: str) -> None:
        """Remove a module and all evidence associated with that generation."""
        key = self._cache_key(path)
        self.modules.pop(key, None)
        self.mtimes.pop(key, None)
        self._stamps.pop(key, None)
        self._dependencies.pop(key, None)
        self._generations.pop(key, None)

    def clear(self) -> None:
        """Clear cached modules and in-progress import identities."""
        self.modules.clear()
        self.loading.clear()
        self.mtimes.clear()
        self._stamps.clear()
        self._dependencies.clear()
        self._generations.clear()

    def is_loading(self, path: str) -> bool:
        return self._cache_key(path) in self.loading

    def start_loading(self, path: str) -> None:
        self.loading.add(self._cache_key(path))

    def finish_loading(self, path: str) -> None:
        self.loading.discard(self._cache_key(path))
