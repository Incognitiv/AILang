"""Implicit language-runtime modules selected from semantic type usage.

These are AILang modules, not foreign/native runtimes.  Backends may lower
primitive operations (allocation, raw memory, syscalls), but language-level
semantics such as arbitrary-precision arithmetic live in `.ail`.
"""
from __future__ import annotations

from typing import Any, Iterable

from parser import ast as A
from parser.ast import parsed_type_to_str

_BIGINT_MODULE = "stdlib.core.bigint"


def _spec_is_unbounded(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "unbounded"
    try:
        return parsed_type_to_str(value).strip().lower() == "unbounded"
    except (TypeError, ValueError, AttributeError):
        return False


def _mentions_unbounded(value: Any, seen: set[int]) -> bool:
    if value is None or isinstance(value, (int, float, bool, bytes)):
        return False
    if _spec_is_unbounded(value):
        return True
    if isinstance(value, str):
        return False
    obj_id = id(value)
    if obj_id in seen:
        return False
    seen.add(obj_id)
    if isinstance(value, dict):
        return any(
            _mentions_unbounded(k, seen) or _mentions_unbounded(v, seen)
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_mentions_unbounded(item, seen) for item in value)
    if isinstance(value, A.ASTNode):
        return any(_mentions_unbounded(item, seen) for item in vars(value).values())
    return False


def ensure_language_runtime_imports(nodes: Iterable[A.ASTNode]) -> list[A.ASTNode]:
    """Return nodes with required pure-AIL runtime modules imported once."""
    result = list(nodes)
    if not _mentions_unbounded(result, set()):
        return result
    for node in result:
        if isinstance(node, A.Import) and node.module_path == _BIGINT_MODULE:
            return result
        if isinstance(node, A.FromImport) and node.module_path == _BIGINT_MODULE:
            return result
    # Put the implicit language module first so its declarations are available
    # before backend pass-1 declarations and user code generation.
    return [A.Import(_BIGINT_MODULE), *result]
