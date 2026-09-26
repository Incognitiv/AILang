"""Repeated compiler passes must share bounded, immutable literal facts."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

import string_literals
from string_literals import c_literal_bytes, clear_literal_cache


@pytest.fixture(autouse=True)
def empty_literal_cache():
    clear_literal_cache()
    yield
    clear_literal_cache()


@pytest.mark.parametrize("text", ["", "abc", "zażółć", "😀", "x\0y"])
def test_literal_encoding_and_nul_semantics(text: str) -> None:
    expected = None if "\0" in text else text.encode("utf-8")
    assert c_literal_bytes(text) == expected
    assert c_literal_bytes(text) == expected


def test_repeated_passes_encode_once() -> None:
    with patch.object(string_literals, "_encode_c_literal", wraps=string_literals._encode_c_literal) as encode:
        for _ in range(100):
            assert c_literal_bytes("zażółć") == "zażółć".encode("utf-8")
    assert encode.call_count == 1


def test_changed_literal_has_separate_facts() -> None:
    assert c_literal_bytes("abc") == b"abc"
    assert c_literal_bytes("abd") == b"abd"
    assert c_literal_bytes("abc") == b"abc"


def test_large_literals_are_not_retained() -> None:
    text = "x" * (string_literals.LITERAL_CACHE_MAX_CHARS + 1)
    with patch.object(string_literals, "_encode_c_literal", wraps=string_literals._encode_c_literal) as encode:
        c_literal_bytes(text)
        c_literal_bytes(text)
    assert encode.call_count == 2
    assert string_literals._cached_c_literal.cache_info().currsize == 0


def test_entry_budget_evicts_old_literals() -> None:
    for index in range(2 * string_literals.LITERAL_CACHE_ENTRIES):
        c_literal_bytes(f"literal-{index}")
    assert string_literals._cached_c_literal.cache_info().currsize == string_literals.LITERAL_CACHE_ENTRIES


def test_explicit_clear_releases_entries() -> None:
    c_literal_bytes("old")
    clear_literal_cache()
    assert string_literals._cached_c_literal.cache_info().currsize == 0


def test_invalid_unicode_is_not_silently_replaced() -> None:
    with pytest.raises(UnicodeEncodeError):
        c_literal_bytes("\ud800")
