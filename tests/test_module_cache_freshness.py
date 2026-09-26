"""Focused module-cache regressions; no compiler/backend toolchain required."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from compiler.module_cache import ModuleCache

if TYPE_CHECKING:
    from compiler.modules import Module


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.ail"
    path.write_text("int value = 1\n", encoding="utf-8")
    return path


def store(cache: ModuleCache, path: Path, module_path: str | None = None) -> Module:
    module = cast(
        "Module",
        SimpleNamespace(path=str(path) if module_path is None else module_path),
    )
    cache.put(str(path), module)
    return module


def test_uncached_source_is_stale(source_file: Path) -> None:
    assert ModuleCache().is_stale(str(source_file))


def test_unchanged_source_is_reused(source_file: Path) -> None:
    cache = ModuleCache()
    module = store(cache, source_file)
    assert not cache.is_stale(str(source_file))
    assert cache.get(str(source_file)) is module


@pytest.mark.parametrize("delta_ns", [2_000_000_000, -2_000_000_000])
def test_timestamp_change_in_either_direction_is_stale(
    source_file: Path, delta_ns: int
) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    info = source_file.stat()
    os.utime(source_file, ns=(info.st_atime_ns, info.st_mtime_ns + delta_ns))
    assert cache.is_stale(str(source_file))


def test_size_change_with_restored_mtime_is_stale(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    info = source_file.stat()
    source_file.write_text("int value = 123456789\n", encoding="utf-8")
    os.utime(source_file, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert source_file.stat().st_mtime_ns == info.st_mtime_ns
    assert cache.is_stale(str(source_file))


def test_replacement_with_same_size_and_mtime_is_stale(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    info = source_file.stat()
    replacement = source_file.with_suffix(".replacement")
    replacement.write_text("int value = 2\n", encoding="utf-8")
    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.replace(replacement, source_file)
    assert source_file.stat().st_size == info.st_size
    assert source_file.stat().st_mtime_ns == info.st_mtime_ns
    assert cache.is_stale(str(source_file))


def test_deleted_source_is_stale(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    source_file.unlink()
    assert cache.is_stale(str(source_file))


def test_absent_freshness_evidence_is_stale(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file, module_path="")
    assert cache.is_stale(str(source_file))


def test_unreadable_metadata_is_stale(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    with patch("os.stat", side_effect=PermissionError("denied")):
        assert cache.is_stale(str(source_file))


def test_failed_replacement_does_not_keep_old_stamp(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    with patch("os.stat", side_effect=PermissionError("denied")):
        store(cache, source_file)
    assert cache.is_stale(str(source_file))


def test_mismatched_module_path_is_not_fresh(source_file: Path, tmp_path: Path) -> None:
    cache = ModuleCache()
    other = tmp_path / "other.ail"
    other.write_bytes(source_file.read_bytes())
    info = source_file.stat()
    os.utime(other, ns=(info.st_atime_ns, info.st_mtime_ns))
    store(cache, source_file, module_path=str(other))
    assert cache.is_stale(str(source_file))


def test_invalidate_removes_the_entry(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    cache.invalidate(str(source_file))
    assert cache.get(str(source_file)) is None
    assert cache.is_stale(str(source_file))
    assert not cache.mtimes


def test_clear_removes_entries_and_loading_state(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    cache.start_loading(str(source_file))
    cache.clear()
    assert cache.get(str(source_file)) is None
    assert cache.is_stale(str(source_file))
    assert not cache.is_loading(str(source_file))
    assert not cache.mtimes


def test_path_aliases_share_one_entry(source_file: Path) -> None:
    cache = ModuleCache()
    module = store(cache, source_file)
    alias = str(source_file.parent) + os.sep + "." + os.sep + source_file.name
    assert cache.get(alias) is module
    assert not cache.is_stale(alias)
    cache.start_loading(alias)
    assert cache.is_loading(str(source_file))
    cache.finish_loading(str(source_file))
    assert not cache.is_loading(alias)


def test_cache_hit_does_not_reread_source_bytes(source_file: Path) -> None:
    cache = ModuleCache()
    module = store(cache, source_file)
    with patch("builtins.open", side_effect=AssertionError("unexpected file read")):
        with patch("io.open", side_effect=AssertionError("unexpected file read")):
            assert not cache.is_stale(str(source_file))
            assert cache.get(str(source_file)) is module


def test_repeated_replacements_refresh_evidence(source_file: Path) -> None:
    cache = ModuleCache()
    store(cache, source_file)
    source_file.write_text("int value = 12\n", encoding="utf-8")
    assert cache.is_stale(str(source_file))
    module = store(cache, source_file)
    assert not cache.is_stale(str(source_file))
    assert cache.get(str(source_file)) is module
