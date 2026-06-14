"""Shared helpers for typed callback/function-pointer aliases."""

from __future__ import annotations

from typing import Any, cast

CallbackSpec = tuple[str, list[tuple[str, Any]], Any, tuple[str, ...]]


def is_callback_type(spec: object) -> bool:
    """Return true when a parsed type is an AILang callback type tuple."""
    if not isinstance(spec, tuple) or len(spec) < 4:
        return False
    tag, *_rest = spec
    return tag == "fn"


def callback_parts(spec: object) -> tuple[list[tuple[str, Any]], Any, list[str]]:
    """Return callback params, return type, and decorators from a callback type."""
    if not is_callback_type(spec):
        raise TypeError(f"not a callback type: {spec!r}")
    _tag, raw_params, ret_type, raw_decorators = cast(CallbackSpec, spec)
    params = list(raw_params)
    decorators = [str(item).lstrip("@").lower() for item in raw_decorators]
    return params, ret_type, decorators


def resolve_callback_alias(name: str, aliases: dict[str, Any]) -> object | None:
    """Resolve an alias name to a callback type tuple, following alias chains."""
    seen: set[str] = set()
    current = name
    while current in aliases and current not in seen:
        seen.add(current)
        target = aliases[current]
        if is_callback_type(target):
            return target
        if isinstance(target, str):
            current = target
            continue
        return None
    return None
