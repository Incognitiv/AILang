"""Bounded, validated storage for disposable verification-cache entries.

Atomic publication protects readers from partial writes. It does not establish
that a verification result's semantic cache key includes its complete context.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import NoReturn

MAX_CACHE_ENTRY_BYTES = 8 * 1024 * 1024


def _reject_nonfinite(value: str) -> NoReturn:
    """JSON extensions such as NaN must not become valid verification evidence."""
    raise ValueError(f"non-finite JSON number: {value}")


def _validated_results(
    cached: object, now: float, max_age_seconds: float
) -> dict | None:
    """Accept only a dictionary result with usable, nonfuture expiry evidence."""
    if not isinstance(cached, dict):
        return None
    stamp = cached.get("cached_at")
    results = cached.get("results")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return None
    if not isinstance(results, dict):
        return None
    cached_at = float(stamp)
    if not math.isfinite(cached_at) or not 0 <= now - cached_at <= max_age_seconds:
        return None
    return results


def load_results(cache_file: Path, now: float, max_age_seconds: float) -> dict | None:
    """Treat malformed, oversized, inaccessible and expired entries as misses."""
    try:
        with cache_file.open("rb") as source:
            data = source.read(MAX_CACHE_ENTRY_BYTES + 1)
        if len(data) > MAX_CACHE_ENTRY_BYTES:
            return None
        cached = json.loads(data.decode("utf-8"), parse_constant=_reject_nonfinite)
        return _validated_results(cached, now, max_age_seconds)
    except (OSError, ValueError, RecursionError, OverflowError):
        return None


def remove_entry(cache_file: Path) -> bool:
    """Delete one disposable entry without interrupting other cleanup work."""
    try:
        cache_file.unlink()
        return True
    except OSError:
        return False


def publish_results(cache_file: Path, results: dict, now: float) -> None:
    """Publish a complete JSON entry, leaving an old result on write failure."""
    if not isinstance(results, dict):
        return
    try:
        data = json.dumps(
            {"cached_at": now, "results": results}, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        return
    if len(data) > MAX_CACHE_ENTRY_BYTES:
        return
    _publish_bytes(cache_file, data)


def _publish_bytes(cache_file: Path, data: bytes) -> None:
    """Use same-directory replacement; cache durability does not require fsync."""
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=cache_file.parent,
            prefix=f".{cache_file.stem}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            staged = Path(output.name)
            output.write(data)
        os.replace(staged, cache_file)
    except OSError:
        return
    finally:
        if staged is not None:
            remove_entry(staged)
