"""Shared naming helpers for call-expression emission."""

from __future__ import annotations

from typing import Any


def is_string_type(type_name: Any) -> bool:
    return str(type_name).strip().lower() in {"string", "str"}


def string_len_name(name: str) -> str:
    return f"__ailang_{name}_len"
