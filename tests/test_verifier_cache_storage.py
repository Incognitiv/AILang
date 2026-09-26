"""Untrusted cache files must not crash verification or tear published results."""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verifier.cache import VerificationCache

CODE = "answer = 42\n"
PRESET = "strict"
RESULT = {"passed": True, "summary": "complete", "issues": []}


@pytest.fixture
def cache(tmp_path: Path) -> VerificationCache:
    return VerificationCache(tmp_path / "cache")


def entry_path(cache: VerificationCache) -> Path:
    return cache._cache_path(cache._compute_hash(CODE, PRESET))


def store_raw(cache: VerificationCache, data: object) -> Path:
    path = entry_path(cache)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_roundtrip_and_independent_results(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    result = cache.get(CODE, PRESET)
    assert result == RESULT
    result["summary"] = "mutated by caller"
    assert cache.get(CODE, PRESET) == RESULT
    assert cache.get("answer = 43\n", PRESET) is None
    assert cache.get(CODE, "fast") is None


@pytest.mark.parametrize("payload", [None, [], 42, "text", True, {}])
def test_wrong_envelope_is_a_miss(cache: VerificationCache, payload: object) -> None:
    store_raw(cache, payload)
    assert cache.get(CODE, PRESET) is None


@pytest.mark.parametrize(
    "stamp", [None, "today", True, [], {}, float("nan"), float("inf"), 10**1000]
)
def test_invalid_timestamp_is_a_miss(cache: VerificationCache, stamp: object) -> None:
    store_raw(cache, {"cached_at": stamp, "results": RESULT})
    assert cache.get(CODE, PRESET) is None


def test_future_timestamp_is_not_fresh(cache: VerificationCache) -> None:
    store_raw(cache, {"cached_at": time.time() + 86400, "results": RESULT})
    assert cache.get(CODE, PRESET) is None


@pytest.mark.parametrize("results", [None, [], "passed", 1, True])
def test_wrong_results_shape_is_a_miss(cache: VerificationCache, results: object) -> None:
    store_raw(cache, {"cached_at": time.time(), "results": results})
    assert cache.get(CODE, PRESET) is None


@pytest.mark.parametrize("data", [b'{"cached_at":', b"\xff\xfe\x00", b"[" * 2000])
def test_unreadable_entry_is_a_miss(cache: VerificationCache, data: bytes) -> None:
    entry_path(cache).write_bytes(data)
    assert cache.get(CODE, PRESET) is None


def test_expired_lookup_does_not_delete_a_concurrently_replaceable_path(
    cache: VerificationCache,
) -> None:
    path = store_raw(cache, {"cached_at": time.time() - 8 * 86400, "results": RESULT})
    assert cache.get(CODE, PRESET) is None
    assert path.exists()
    assert cache.clear_expired() == 1
    assert not path.exists()


def test_bad_serialization_preserves_previous_entry(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    before = entry_path(cache).read_bytes()
    cache.set(CODE, PRESET, {"bad": object()})
    assert entry_path(cache).read_bytes() == before
    assert cache.get(CODE, PRESET) == RESULT


def test_nonfinite_result_is_not_published(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    cache.set(CODE, PRESET, {"score": float("nan")})
    assert cache.get(CODE, PRESET) == RESULT


def test_failed_atomic_replace_preserves_old_entry(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    with patch("os.replace", side_effect=OSError("injected publication failure")):
        cache.set(CODE, PRESET, {"passed": False})
    assert cache.get(CODE, PRESET) == RESULT
    assert list(cache.cache_dir.iterdir()) == [entry_path(cache)]


def test_old_result_visible_until_complete_publication(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    replace = os.replace

    def inspect_then_replace(source: Path, destination: Path) -> None:
        assert cache.get(CODE, PRESET) == RESULT
        pending = json.loads(Path(source).read_bytes())
        assert pending["results"] == {"passed": False}
        assert Path(source).parent == Path(destination).parent
        replace(source, destination)

    with patch("os.replace", side_effect=inspect_then_replace) as publish:
        cache.set(CODE, PRESET, {"passed": False})
    assert publish.call_count == 1
    assert cache.get(CODE, PRESET) == {"passed": False}


def test_unavailable_cache_directory_does_not_block_verification(tmp_path: Path) -> None:
    with patch.object(Path, "mkdir", side_effect=PermissionError("cache denied")):
        cache = VerificationCache(tmp_path / "unavailable")
    assert cache.get(CODE, PRESET) is None
    cache.set(CODE, PRESET, RESULT)
    assert cache.get(CODE, PRESET) is None


def test_clear_continues_after_one_inaccessible_entry(cache: VerificationCache) -> None:
    first = cache.cache_dir / "first.json"
    second = cache.cache_dir / "second.json"
    first.write_text("{}")
    second.write_text("{}")
    unlink = Path.unlink

    def remove(path: Path, **kwargs: object) -> None:
        if path == first:
            raise PermissionError("locked")
        unlink(path, **kwargs)

    with patch.object(Path, "glob", return_value=iter([first, second])):
        with patch.object(Path, "unlink", autospec=True, side_effect=remove):
            assert cache.clear() == 1
    assert first.exists()
    assert not second.exists()


def test_prune_handles_corruption_and_keeps_valid_entry(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)
    (cache.cache_dir / "bad.json").write_text("[]")
    (cache.cache_dir / "unicode.json").write_bytes(b"\xff")
    (cache.cache_dir / "future.json").write_text(
        json.dumps({"cached_at": time.time() + 86400, "results": RESULT})
    )
    assert cache.clear_expired() == 3
    assert cache.get(CODE, PRESET) == RESULT


def test_parallel_publication_never_exposes_partial_json(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, RESULT)

    def write(marker: str) -> None:
        for _ in range(20):
            cache.set(CODE, PRESET, {"passed": True, "marker": marker * 2000})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, marker) for marker in ("a", "b")]
        for _ in range(100):
            envelope = json.loads(entry_path(cache).read_bytes())
            assert isinstance(envelope["results"], dict)
        for future in futures:
            future.result()
    assert cache.get(CODE, PRESET)["marker"] in {"a" * 2000, "b" * 2000}
    assert len(list(cache.cache_dir.iterdir())) == 1


def test_oversized_read_is_a_miss(cache: VerificationCache) -> None:
    from verifier import cache_storage

    store_raw(cache, {"cached_at": time.time(), "results": {"data": "x" * 2048}})
    with patch.object(cache_storage, "MAX_CACHE_ENTRY_BYTES", 1024):
        assert cache.get(CODE, PRESET) is None


def test_oversized_write_preserves_old_entry(cache: VerificationCache) -> None:
    from verifier import cache_storage

    cache.set(CODE, PRESET, RESULT)
    with patch.object(cache_storage, "MAX_CACHE_ENTRY_BYTES", 1024):
        cache.set(CODE, PRESET, {"data": "x" * 2048})
    assert cache.get(CODE, PRESET) == RESULT


def test_nested_nonfinite_read_is_a_miss(cache: VerificationCache) -> None:
    store_raw(cache, {"cached_at": time.time(), "results": {"score": float("nan")}})
    assert cache.get(CODE, PRESET) is None


def test_empty_dictionary_is_valid_result(cache: VerificationCache) -> None:
    cache.set(CODE, PRESET, {})
    assert cache.get(CODE, PRESET) == {}


def test_missing_directory_can_become_available(cache: VerificationCache) -> None:
    cache.cache_dir.rmdir()
    cache.set(CODE, PRESET, RESULT)
    assert cache.get(CODE, PRESET) is None
    cache.cache_dir.mkdir()
    cache.set(CODE, PRESET, RESULT)
    assert cache.get(CODE, PRESET) == RESULT


def test_publish_does_not_follow_existing_symlink(cache: VerificationCache) -> None:
    target = cache.cache_dir.parent / "keep.txt"
    target.write_text("must survive", encoding="utf-8")
    try:
        entry_path(cache).symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    cache.set(CODE, PRESET, RESULT)
    assert target.read_text(encoding="utf-8") == "must survive"
    assert not entry_path(cache).is_symlink()
    assert cache.get(CODE, PRESET) == RESULT
