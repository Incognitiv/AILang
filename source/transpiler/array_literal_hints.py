"""Exact integer array literal hints for C backend proofs."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Optional, Tuple

ArrayValues = Tuple[int, ...]
HintKey = Tuple[Optional[str], str]


def _function_scope(owner: Any) -> Optional[str]:
    scope = getattr(owner, "current_function", None)
    if isinstance(scope, str):
        return scope
    return getattr(owner, "_current_function_name", None)


def update_array_literal_hints(owner: Any, var_name: str, expr: A.ASTNode) -> None:
    hints = getattr(owner, "_array_literal_value_hints", None)
    if not isinstance(hints, dict):
        return
    key = (_function_scope(owner), var_name)
    values = _literal_int_values(expr)
    if values is not None:
        hints[key] = values
        return
    if isinstance(expr, A.Variable):
        propagated = get_array_literal_values(owner, expr.name)
        if propagated is not None:
            hints[key] = propagated
            return
    hints.pop(key, None)


def get_array_literal_values(owner: Any, var_name: str) -> Optional[ArrayValues]:
    hints = getattr(owner, "_array_literal_value_hints", None)
    if not isinstance(hints, dict):
        return None
    scoped = (_function_scope(owner), var_name)
    if scoped in hints:
        return hints[scoped]
    global_key = (None, var_name)
    if global_key in hints:
        return hints[global_key]
    return None


def _literal_int_values(expr: A.ASTNode) -> Optional[ArrayValues]:
    if not isinstance(expr, A.ArrayLit):
        return None
    values: list[int] = []
    for element in expr.elements:
        if not isinstance(element, A.Number) or not isinstance(element.value, int):
            return None
        values.append(int(element.value))
    return tuple(values)
