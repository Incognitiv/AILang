"""A finite-looking JSON exponent and duplicate keys are invalid cache evidence."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verifier.cache import VerificationCache


def write_payload(cache: VerificationCache, payload: str) -> None:
    path = cache._cache_path(cache._compute_hash("x=1", "strict"))
    path.write_text(payload, encoding="utf-8")


@pytest.mark.parametrize("number", ["1e400", "-1e400", "1.8e308", "-1.8e308"])
def test_nested_overflow_is_rejected(tmp_path: Path, number: str) -> None:
    cache = VerificationCache(tmp_path / "cache")
    write_payload(cache, '{"cached_at":' + str(time.time()) +
                  ',"results":{"nested":[{"score":' + number + '}]}}')
    assert cache.get("x=1", "strict") is None


@pytest.mark.parametrize("duplicate", [
    '"cached_at":1,"cached_at":NOW,"results":{}',
    '"cached_at":NOW,"results":{"passed":false},"results":{"passed":true}',
    '"cached_at":NOW,"results":{"passed":false,"passed":true}',
    '"cached_at":NOW,"results":{"nested":[{"score":0,"score":100}]}',
])
def test_duplicate_evidence_fields_are_rejected(tmp_path: Path, duplicate: str) -> None:
    cache = VerificationCache(tmp_path / "cache")
    write_payload(cache, "{" + duplicate.replace("NOW", str(time.time())) + "}")
    assert cache.get("x=1", "strict") is None


def test_large_finite_exponent_remains_supported(tmp_path: Path) -> None:
    cache = VerificationCache(tmp_path / "cache")
    write_payload(cache, '{"cached_at":' + str(time.time()) + ',"results":{"value":1e308}}')
    assert cache.get("x=1", "strict") == {"value": 1e308}
