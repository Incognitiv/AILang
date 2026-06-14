"""C strlen expression helpers for stable dynamic string shapes."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from transpiler.string_length_plan import (
    dynamic_string_length_plan,
    grouped_dynamic_terms,
)


def emit_dynamic_strlen_c(emitter: Any, node: A.ASTNode) -> str | None:
    plan = dynamic_string_length_plan(node)
    if plan is None or not plan.dynamic_terms:
        return None
    if plan.static_total == 0 and plan.dynamic_terms == (node,):
        return None
    terms: list[str] = []
    if plan.static_total:
        terms.append(f"{plan.static_total}LL")
    for term, count in grouped_dynamic_terms(plan):
        term_len = emitter._emit_known_strlen(term)
        if count == 1:
            terms.append(term_len)
        else:
            terms.append(f"({count}LL * ({term_len}))")
    return "(" + " + ".join(terms) + ")" if terms else "0LL"
