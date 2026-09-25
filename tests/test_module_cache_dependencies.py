"""Dependency closure and generation regressions for the RAM module cache."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler import module_cache
from compiler.module_cache import ModuleCache

if TYPE_CHECKING:
    from compiler.modules import Module


def cache_file(cache: ModuleCache, path: Path, *dependencies: Path) -> Module:
    if not path.exists():
        path.write_text("int value = 1\n", encoding="utf-8")
    module = cast("Module", SimpleNamespace(path=str(path)))
    cache.put(str(path), module, dependencies=tuple(map(str, dependencies)))
    return module


def test_changed_leaf_invalidates_unchanged_parent(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, child)
    cache_file(cache, parent, child)
    assert not cache.is_stale(str(parent))
    child.write_text("int value = 1234567\n", encoding="utf-8")
    assert cache.is_stale(str(parent))


def test_rebuilt_child_does_not_bless_old_parent(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, child)
    cache_file(cache, parent, child)
    cache_file(cache, child)
    assert not cache.is_stale(str(child))
    assert cache.is_stale(str(parent))
    cache_file(cache, parent, child)
    assert not cache.is_stale(str(parent))


def test_transitive_leaf_change_reaches_root(tmp_path: Path) -> None:
    cache = ModuleCache()
    leaf, middle, root = (tmp_path / f"{name}.ail" for name in ("leaf", "middle", "root"))
    cache_file(cache, leaf)
    cache_file(cache, middle, leaf)
    cache_file(cache, root, middle)
    leaf.unlink()
    assert cache.is_stale(str(root))


def test_missing_dependency_evidence_is_stale(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, parent, child)
    assert cache.is_stale(str(parent))
    cache_file(cache, child)
    assert cache.is_stale(str(parent))


def test_invalidated_child_invalidates_parent(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, child)
    cache_file(cache, parent, child)
    cache.invalidate(str(child))
    assert cache.is_stale(str(parent))


def test_unrelated_module_is_not_invalidated(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent, other = (tmp_path / f"{name}.ail" for name in ("child", "parent", "other"))
    cache_file(cache, child)
    cache_file(cache, parent, child)
    cache_file(cache, other)
    other.write_text("int value = 1234567\n", encoding="utf-8")
    assert not cache.is_stale(str(parent))


def test_diamond_checks_leaf_once(tmp_path: Path) -> None:
    cache = ModuleCache()
    leaf, left, right, root = (tmp_path / f"{name}.ail" for name in ("leaf", "left", "right", "root"))
    cache_file(cache, leaf)
    cache_file(cache, left, leaf)
    cache_file(cache, right, leaf)
    cache_file(cache, root, left, right)
    with patch.object(module_cache, "_source_stamp", wraps=module_cache._source_stamp) as stamp:
        assert not cache.is_stale(str(root))
    assert stamp.call_count == 4


def test_deep_graph_does_not_use_python_recursion(tmp_path: Path) -> None:
    cache = ModuleCache()
    child: Path | None = None
    for index in range(1100):
        parent = tmp_path / f"module_{index}.ail"
        cache_file(cache, parent, *((child,) if child else ()))
        child = parent
    assert child is not None
    assert not cache.is_stale(str(child))


def test_replacing_parent_discards_old_edges(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, child)
    cache_file(cache, parent, child)
    cache_file(cache, parent)
    child.unlink()
    assert not cache.is_stale(str(parent))


def test_dependency_hit_does_not_read_contents(tmp_path: Path) -> None:
    cache = ModuleCache()
    child, parent = tmp_path / "child.ail", tmp_path / "parent.ail"
    cache_file(cache, child)
    cache_file(cache, parent, child)
    with patch("builtins.open", side_effect=AssertionError("unexpected read")):
        with patch("io.open", side_effect=AssertionError("unexpected read")):
            assert not cache.is_stale(str(parent))


def test_redirected_dependency_is_stale(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    cache = ModuleCache()
    old, new, alias, parent = (tmp_path / f"{name}.ail" for name in ("old", "new", "alias", "parent"))
    old.write_text("int value = 1\n", encoding="utf-8")
    new.write_text("int value = 2\n", encoding="utf-8")
    try:
        alias.symlink_to(old)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    cache_file(cache, alias)
    cache_file(cache, parent, alias)
    alias.unlink()
    alias.symlink_to(new)
    assert cache.is_stale(str(parent))
