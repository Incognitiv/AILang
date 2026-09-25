"""Iterative dependency loading with the existing import/error semantics."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from parser.ast import FromImport, Import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from compiler.modules import Module, ModuleLoader


@dataclass
class _LoadFrame:
    module: Module
    imports: Iterator[Import | FromImport]
    incoming: Import | FromImport | None


def _cached_or_path(loader: ModuleLoader, name: str) -> tuple[Module | None, str]:
    path = loader.resolve_module_path(name)
    if not path:
        raise ImportError(f"Cannot find module '{name}'")
    if loader.cache.is_stale(path):
        loader.cache.invalidate(path)
    cached = loader.cache.get(path)
    if cached is None and loader.cache.is_loading(path):
        raise ImportError(f"Circular import detected: '{name}'")
    return cached, path


def _open_frame(
    loader: ModuleLoader, name: str, path: str, incoming: Import | FromImport | None
) -> _LoadFrame:
    loader.cache.start_loading(path)
    try:
        module = loader._load_file(name, path)
    except BaseException:
        loader.cache.finish_loading(path)
        raise
    imports = (node for node in module.ast if isinstance(node, (Import, FromImport)))
    return _LoadFrame(module, imports, incoming)


def _include(
    loader: ModuleLoader, parent: Module, child: Module, node: Import | FromImport
) -> None:
    parent.dependencies.append(child.path)
    names = node.names if isinstance(node, FromImport) else None
    loader._merge_dependency_exports(
        parent, child, node.module_path, requested_names=names
    )


def _warn(node: Import, error: ImportError) -> None:
    print(f"Warning: Failed to import '{node.module_path}': {error}", file=sys.stderr)


def _advance(loader: ModuleLoader, frames: list[_LoadFrame]) -> Module | None:
    frame = frames[-1]
    loader.current_file = frame.module.path
    node = next(frame.imports, None)
    if node is None:
        module = frame.module
        loader.cache.put(module.path, module, dependencies=tuple(module.dependencies))
        loader.cache.finish_loading(module.path)
        frames.pop()
        if not frames:
            return module
        if frame.incoming is not None:
            _include(loader, frames[-1].module, module, frame.incoming)
        return None
    try:
        child, path = _cached_or_path(loader, node.module_path)
        if child is not None:
            _include(loader, frame.module, child, node)
        else:
            frames.append(_open_frame(loader, node.module_path, path, node))
    except ImportError as error:
        if isinstance(node, FromImport):
            raise
        _warn(node, error)
    return None


def _unwind(
    loader: ModuleLoader, frames: list[_LoadFrame], error: ImportError
) -> None:
    """A failed selective import propagates until a plain import can warn."""
    while frames:
        frame = frames.pop()
        loader.cache.finish_loading(frame.module.path)
        if isinstance(frame.incoming, Import):
            _warn(frame.incoming, error)
            return
    raise error


def load_module_graph(loader: ModuleLoader, name: str) -> Module:
    """Load each uncached module once without using the Python call stack."""
    cached, path = _cached_or_path(loader, name)
    if cached is not None:
        return cached
    previous = loader.current_file
    frames: list[_LoadFrame] = []
    try:
        frames.append(_open_frame(loader, name, path, None))
        while frames:
            try:
                result = _advance(loader, frames)
            except ImportError as error:
                _unwind(loader, frames, error)
                continue
            if result is not None:
                return result
        raise ImportError(f"Could not load module '{name}'")
    finally:
        for frame in frames:
            loader.cache.finish_loading(frame.module.path)
        loader.current_file = previous
