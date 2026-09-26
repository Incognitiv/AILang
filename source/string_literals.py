"""Bounded reuse of UTF-8 facts for immutable compiler string literals.

This cache is not a runtime string ABI. Large literals bypass it so retaining
one-off source texts cannot consume the entire compiler memory budget.
"""

from __future__ import annotations

from functools import lru_cache

LITERAL_CACHE_ENTRIES = 128
LITERAL_CACHE_MAX_CHARS = 4096


def _encode_c_literal(value: str) -> bytes | None:
    """Decline a proof when an embedded NUL changes C-string semantics."""
    if "\0" in value:
        return None
    return value.encode("utf-8")


@lru_cache(maxsize=LITERAL_CACHE_ENTRIES)
def _cached_c_literal(value: str) -> bytes | None:
    return _encode_c_literal(value)


def c_literal_bytes(value: str) -> bytes | None:
    """Encode a short literal once across length, indexing and comparison passes."""
    if len(value) > LITERAL_CACHE_MAX_CHARS:
        return _encode_c_literal(value)
    return _cached_c_literal(value)


def clear_literal_cache() -> None:
    """Release retained literal facts at an embedding application's request."""
    _cached_c_literal.cache_clear()
