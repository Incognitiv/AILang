"""Loop-pattern helpers for RangeFactsAnalyzer."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeGuard

from parser import ast as A

from ast_access import body_at

from .codegen_int_ranges import _clamp_if_pattern
from .range_facts_loop_state import (
    dict_state_from_facts,
    expr_interval_with_loop_state,
)
from .range_facts_reduction import (
    _nested_reduction_budget,
    _scalar_reduction_budget,
    _self_reduction_budget,
    _symbolic_step_one_counter_range,
    _while_counter_bound,
)


def while_true_break_guard_bounds(
    node: A.While,
    *,
    func_scope: str | None,
    facts: Any,
    scope: dict[str, Any],
) -> tuple[dict[str, tuple[int, int]], bool]:
    """Derive conservative in-loop bounds from while-true break guards.

    Recognized shape:

    while true then
        if i >= LIMIT then
            break
        end
        ...
    end

    Returns ({var_name: (low, high)}, ignore_first_break_guard) on match.
    """
    if not (isinstance(node.cond, A.Bool) and bool(node.cond.value)):
        return {}, False
    if not node.body:
        return {}, False
    first = body_at(node, 0)
    if not isinstance(first, A.If):
        return {}, False
    if first.else_body:
        return {}, False
    if len(first.then_body) != 1 or not isinstance(first.then_body[0], A.Break):
        return {}, False
    guard = first.cond
    if not isinstance(guard, A.BinaryOp):
        return {}, False

    op = guard.op
    left = guard.left
    right = guard.right
    if isinstance(left, A.Number) and isinstance(right, A.Variable):
        op = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(op, op)
        left, right = right, left
    if not isinstance(left, A.Variable):
        return {}, False
    if op not in {"<", "<=", ">", ">="}:
        return {}, False

    bound_rng = facts._expr_interval(right, func_scope, dict(scope))
    if bound_rng is None or bound_rng.low != bound_rng.high:
        return {}, False
    bound = bound_rng.low

    current = scope.get(left.name)
    if current is None:
        return {}, False

    low = current.low
    high = current.high
    if op == ">=":
        high = min(high, bound - 1)
    elif op == ">":
        high = min(high, bound)
    elif op == "<=":
        low = max(low, bound + 1)
    elif op == "<":
        low = max(low, bound)
    if low > high:
        return {}, False

    return {left.name: (low, high)}, True


def is_branch_heavy_loop_body(
    nodes: Iterable[A.ASTNode],
    *,
    ignore_first_break_guard: bool = False,
    walk_ast: Callable[[A.ASTNode], Iterable[A.ASTNode]],
) -> bool:
    """Return True when loop body is branch-heavy for conservative analysis."""
    branchy = {
        "If",
        "Match",
        "TryExcept",
        "While",
        "DoWhile",
        "For",
        "Foreach",
        "Repeat",
        "Loop",
    }
    node_list = list(nodes)
    for idx, node in enumerate(node_list):
        if ignore_first_break_guard and idx == 0 and _is_break_guard_if(node):
            continue
        if _is_break_guard_if(node):
            continue
        for child in walk_ast(node):
            if type(child).__name__ in branchy:
                return True
    return False


def _is_break_guard_if(node: A.ASTNode) -> TypeGuard[A.If]:
    return (
        isinstance(node, A.If)
        and not node.else_body
        and len(node.then_body) == 1
        and isinstance(node.then_body[0], A.Break)
    )


def derive_specialized_while_ranges(
    node: A.While,
    *,
    func_scope: str | None,
    facts: Any,
    scope: dict[str, Any],
) -> tuple[dict[str, tuple[int, int]], set[str]]:
    """Derive conservative while-loop refinements for hot fixed-trip patterns.

    The specialization is intentionally strict:
    - counter loop: `while i < BOUND then ... i = i + STEP ... end`
    - optional nested reduction: fixed-range inner loop adding array/slice values

    Returns (refinements, preserve_assigned). Empty outputs when the shape
    is not recognized.
    """
    refinements: dict[str, tuple[int, int]] = {}
    preserve: set[str] = set()

    if isinstance(node.cond, A.Bool) and bool(node.cond.value):
        acc_name, total_budget = _self_reduction_budget(
            node.body, facts=facts, func_scope=func_scope, scope=scope
        )
        if acc_name is not None and total_budget is not None and total_budget >= 0:
            acc_current = scope.get(acc_name)
            int64_max = (1 << 63) - 1
            if acc_current is not None and acc_current.low >= 0:
                total_high = acc_current.high + total_budget
                if total_high <= int64_max:
                    refinements[acc_name] = (acc_current.low, total_high)
                    preserve.add(acc_name)
        return refinements, preserve

    bounded_counter_name, counter_bound, step = _while_counter_bound(
        node, facts, func_scope, scope
    )
    if bounded_counter_name is None or counter_bound is None or step <= 0:
        symbolic = _symbolic_step_one_counter_range(node, scope)
        if symbolic is not None:
            symbolic_counter_name, low, high = symbolic
            refinements[symbolic_counter_name] = (low, high)
            preserve.add(symbolic_counter_name)
        return refinements, preserve

    current = scope.get(bounded_counter_name)
    if current is None:
        return refinements, preserve
    start = current.low
    if start > counter_bound:
        return refinements, preserve

    refinements[bounded_counter_name] = (start, counter_bound)
    preserve.add(bounded_counter_name)

    trip_count = ((counter_bound - start) // step) + 1
    if trip_count <= 0:
        return refinements, preserve

    loop_counter_scope = dict(scope)
    loop_counter_scope[bounded_counter_name] = type(current)(start, counter_bound)
    clamped = _clamped_accumulator_ranges(
        node.body,
        facts=facts,
        func_scope=func_scope,
        scope=loop_counter_scope,
        counter_name=bounded_counter_name,
    )
    if clamped:
        refinements.update(clamped)
        preserve.update(clamped)

    acc_name, per_iter_budget = _nested_reduction_budget(
        node.body, facts=facts, func_scope=func_scope, scope=scope
    )
    if acc_name is None or per_iter_budget is None or per_iter_budget < 0:
        acc_name, per_iter_budget = _scalar_reduction_budget(
            node.body,
            facts=facts,
            func_scope=func_scope,
            scope=loop_counter_scope,
            counter_name=bounded_counter_name,
        )
    if acc_name is None or per_iter_budget is None or per_iter_budget < 0:
        return refinements, preserve

    acc_current = scope.get(acc_name)
    if acc_current is None or acc_current.low < 0:
        return refinements, preserve

    total_high = acc_current.high + (trip_count * per_iter_budget)
    int64_max = (1 << 63) - 1
    if total_high > int64_max:
        return refinements, preserve

    refinements[acc_name] = (acc_current.low, total_high)
    preserve.add(acc_name)
    return refinements, preserve


def _clamped_accumulator_ranges(
    body: Iterable[A.ASTNode],
    *,
    facts: Any,
    func_scope: str | None,
    scope: dict[str, Any],
    counter_name: str,
) -> dict[str, tuple[int, int]]:
    clamp_limits: dict[str, int] = {}
    update_exprs: dict[str, A.ASTNode] = {}
    for stmt in body:
        clamp = _clamp_if_pattern(stmt)
        if clamp is not None:
            var_name, limit = clamp
            clamp_limits[var_name] = limit
            continue
        if not isinstance(stmt, A.Assign) or stmt.var_name == counter_name:
            continue
        if stmt.var_name in update_exprs:
            return {}
        update_exprs[stmt.var_name] = stmt.value
    if not clamp_limits or set(clamp_limits) != set(update_exprs):
        return {}

    refined: dict[str, tuple[int, int]] = {}
    trial_scope = dict(scope)
    for var_name, limit in clamp_limits.items():
        current = scope.get(var_name)
        if current is None or current.low < 0 or current.high > limit:
            return {}
        trial_scope[var_name] = type(current)(0, limit)
        refined[var_name] = (0, limit)

    dict_state = dict_state_from_facts(facts, func_scope)
    for var_name, limit in clamp_limits.items():
        update_range = expr_interval_with_loop_state(
            update_exprs[var_name],
            facts=facts,
            func_scope=func_scope,
            scope=trial_scope,
            dict_state=dict_state,
            transient_strings={},
        )
        if (
            update_range is None
            or update_range.low < 0
            or update_range.high > (limit * 2)
        ):
            return {}
    return refined
