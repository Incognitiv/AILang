"""Planning helpers for C strlen lowering.

The planner separates compile-time literal byte lengths from dynamic string
terms in simple string shapes.  It does not guess ownership or evaluate
expressions; unknown terms are left for the normal strlen/cache paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from parser import ast as A
from typing import Iterator


@dataclass(frozen=True)
class StringLengthPlan:
    static_total: int
    dynamic_terms: tuple[A.ASTNode, ...]


def _literal_byte_length(node: A.ASTNode) -> int | None:
    if not isinstance(node, A.StringLit):
        return None
    if "\0" in node.value:
        return None
    return len(node.value.encode("utf-8"))


def _merge(left: StringLengthPlan, right: StringLengthPlan) -> StringLengthPlan:
    return StringLengthPlan(
        left.static_total + right.static_total,
        left.dynamic_terms + right.dynamic_terms,
    )


def dynamic_string_length_plan(node: A.ASTNode) -> StringLengthPlan | None:
    """Return a strlen plan for static literals and `+`/`plus` chains.

    A single unknown node becomes one dynamic term; callers can reject the
    trivial `strlen(node)` case to avoid recursive self-lowering.
    """
    literal_len = _literal_byte_length(node)
    if literal_len is not None:
        return StringLengthPlan(literal_len, ())

    if isinstance(node, A.BinaryOp) and node.op in {"+", "plus"}:
        left = dynamic_string_length_plan(node.left)
        right = dynamic_string_length_plan(node.right)
        if left is None or right is None:
            return None
        return _merge(left, right)

    return StringLengthPlan(0, (node,))


def grouped_dynamic_terms(
    plan: StringLengthPlan,
) -> Iterator[tuple[A.ASTNode, int]]:
    """Yield adjacent equal-by-object terms as `(term, count)` pairs."""
    current: A.ASTNode | None = None
    count = 0
    for term in plan.dynamic_terms:
        if current is term:
            count += 1
            continue
        if current is not None:
            yield current, count
        current = term
        count = 1
    if current is not None:
        yield current, count
