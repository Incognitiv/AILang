"""C strlen cache lookup helpers.

This module intentionally never fabricates a length.  It only returns values
from cache maps that other analysis passes have already attached to the
emitter/transpiler object.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any


def lookup_c_strlen_cache(emitter: Any, node: A.Variable) -> str | None:
    """Return a cached C length expression for `node` when one is known."""
    cache = getattr(emitter, "_c_strlen_cache", None)
    if isinstance(cache, dict):
        value = cache.get(node.name)
        if isinstance(value, str) and value:
            return value

    local_cache = getattr(emitter, "_strlen_cache", None)
    if isinstance(local_cache, dict):
        value = local_cache.get(node.name)
        if isinstance(value, str) and value:
            return value

    return None


def enter_strlen_cache_control(owner: Any) -> None:
    stack = getattr(owner, "_strlen_cache_control_stack", None)
    if stack is None:
        stack = []
        setattr(owner, "_strlen_cache_control_stack", stack)
    snapshot: dict[str, dict[Any, Any]] = {}
    for name in ("_strlen_cache", "_c_strlen_cache"):
        value = getattr(owner, name, None)
        if isinstance(value, dict):
            snapshot[name] = dict(value)
    stack.append(snapshot)


def leave_strlen_cache_control(owner: Any) -> None:
    stack = getattr(owner, "_strlen_cache_control_stack", None)
    if not stack:
        return
    snapshot = stack.pop()
    for name in ("_strlen_cache", "_c_strlen_cache"):
        if name in snapshot:
            setattr(owner, name, snapshot[name])
