"""Loop-pattern helpers for RangeFactsAnalyzer."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple, TypeGuard

from ast_access import arg_at, body_at

from .codegen_int_ranges import _clamp_if_pattern
from .range_facts_loop_state import (
    dict_state_from_facts,
    expr_interval_with_loop_state,
    literal_dict_assignment,
    self_accumulator_growth_terms,
)
from .range_facts_types import Interval, StringInfo, string_info_from_format_call


def while_true_break_guard_bounds(
    node: A.While,
    *,
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
) -> Tuple[Dict[str, Tuple[int, int]], bool]:
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


def derive_specialized_while_ranges(
    node: A.While,
    *,
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
) -> Tuple[Dict[str, Tuple[int, int]], Set[str]]:
    """Derive conservative while-loop refinements for hot fixed-trip patterns.

    The specialization is intentionally strict:
    - counter loop: `while i < BOUND then ... i = i + STEP ... end`
    - optional nested reduction: fixed-range inner loop adding array/slice values

    Returns (refinements, preserve_assigned). Empty outputs when the shape
    is not recognized.
    """
    refinements: Dict[str, Tuple[int, int]] = {}
    preserve: Set[str] = set()

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
    func_scope: Optional[str],
    scope: Dict[str, Any],
    counter_name: str,
) -> Dict[str, Tuple[int, int]]:
    clamp_limits: Dict[str, int] = {}
    update_exprs: Dict[str, A.ASTNode] = {}
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

    refined: Dict[str, Tuple[int, int]] = {}
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


def _scalar_reduction_budget(
    body: Iterable[A.ASTNode],
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
    counter_name: str,
) -> Tuple[Optional[str], Optional[int]]:
    """Return max per-iteration growth for `acc = acc + expr` loops.

    This is deliberately narrower than a general recurrence solver. It accepts
    only one accumulator, self-adds of nonnegative bounded expressions, and
    self-subs of nonnegative bounded expressions. Subtractions are allowed
    because they cannot increase the accumulator high watermark.
    """
    if _assignment_count_for_var(body, counter_name) != 1:
        return None, None
    acc_name: Optional[str] = None
    budget = 0
    found_growth = False
    transient_strings: Dict[str, StringInfo] = {}
    dict_state = dict_state_from_facts(facts, func_scope)
    local_scope = dict(scope)
    for stmt in body:
        dict_write = literal_dict_assignment(stmt)
        if dict_write is not None:
            dict_name, key, value_expr = dict_write
            value_rng = expr_interval_with_loop_state(
                value_expr,
                facts=facts,
                func_scope=func_scope,
                scope=local_scope,
                dict_state=dict_state,
                transient_strings=transient_strings,
            )
            if value_rng is None:
                dict_state.get(dict_name, {}).pop(key, None)
            else:
                dict_state.setdefault(dict_name, {})[key] = value_rng
            continue
        if not isinstance(stmt, A.Assign):
            continue
        assign = stmt
        if assign.var_name == counter_name:
            continue
        assigned_info = _string_info_from_assignment(
            assign.value, facts=facts, func_scope=func_scope, scope=local_scope
        )
        if assigned_info is None:
            transient_strings.pop(assign.var_name, None)
        else:
            transient_strings[assign.var_name] = assigned_info
        value_rng = expr_interval_with_loop_state(
            assign.value,
            facts=facts,
            func_scope=func_scope,
            scope=local_scope,
            dict_state=dict_state,
            transient_strings=transient_strings,
        )
        growth_terms = self_accumulator_growth_terms(assign.var_name, assign.value)
        if growth_terms is None:
            if assign.var_name == acc_name:
                return None, None
            if value_rng is None:
                local_scope.pop(assign.var_name, None)
            else:
                local_scope[assign.var_name] = value_rng
            continue
        if acc_name is None:
            acc_name = assign.var_name
        if assign.var_name != acc_name:
            if value_rng is None:
                local_scope.pop(assign.var_name, None)
            else:
                local_scope[assign.var_name] = value_rng
                continue
            return None, None
        for term in growth_terms:
            rhs_rng = expr_interval_with_loop_state(
                term,
                facts=facts,
                func_scope=func_scope,
                scope=local_scope,
                dict_state=dict_state,
                transient_strings=transient_strings,
            )
            if rhs_rng is None or rhs_rng.low < 0:
                return None, None
            budget += int(rhs_rng.high)
            found_growth = True
        if value_rng is None:
            local_scope.pop(assign.var_name, None)
        else:
            local_scope[assign.var_name] = value_rng
    if acc_name is None or not found_growth:
        return None, None
    return acc_name, budget


def _string_info_from_assignment(
    expr: A.ASTNode,
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
) -> Optional[StringInfo]:
    if isinstance(expr, A.StringLit):
        from .range_facts_types import string_info_from_literal

        return string_info_from_literal(expr.value)
    if isinstance(expr, A.Cast):
        return _string_info_from_assignment(
            expr.expr, facts=facts, func_scope=func_scope, scope=scope
        )
    if not isinstance(expr, A.Call) or len(expr.args or []) != 1:
        return None
    arg_rng = facts._expr_interval(arg_at(expr, 0), func_scope, dict(scope))
    if arg_rng is None:
        return None
    return string_info_from_format_call(expr.name, arg_rng.low, arg_rng.high)


def _expr_interval_with_transient_strings(
    expr: A.ASTNode,
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
    transient_strings: Dict[str, StringInfo],
) -> Optional[Interval]:
    if isinstance(expr, A.Call) and expr.name in {"len", "strlen"}:
        if len(expr.args or []) != 1:
            return None
        arg = arg_at(expr, 0)
        if isinstance(arg, A.StringLit):
            return Interval(len(arg.value), len(arg.value))
        if isinstance(arg, A.Variable):
            info = transient_strings.get(arg.name)
            if info is None:
                info = facts.get_string_info(func_scope, arg.name)
            if info is not None:
                return Interval(info.min_len, info.max_len)
        return None
    if isinstance(expr, A.Cast):
        return _expr_interval_with_transient_strings(
            expr.expr,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            transient_strings=transient_strings,
        )
    if isinstance(expr, A.BinaryOp) and expr.op in {"+", "-", "*"}:
        left = _expr_interval_with_transient_strings(
            expr.left,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            transient_strings=transient_strings,
        )
        right = _expr_interval_with_transient_strings(
            expr.right,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            transient_strings=transient_strings,
        )
        if left is None or right is None:
            return None
        return facts._binary_interval(expr.op, left, right)
    return facts._expr_interval(expr, func_scope, dict(scope))


def _iter_assignments(nodes: Iterable[A.ASTNode]) -> Iterable[A.Assign]:
    for node in nodes:
        if isinstance(node, A.Assign):
            yield node
        for child in _iter_ast_children(node):
            yield from _iter_assignments((child,))


def _is_self_accumulator_write(node: A.Assign) -> bool:
    value = node.value
    return (
        isinstance(value, A.BinaryOp)
        and value.op in {"+", "-"}
        and isinstance(value.left, A.Variable)
        and value.left.name == node.var_name
    )


def _symbolic_step_one_counter_range(
    node: A.While, scope: Dict[str, Any]
) -> Optional[Tuple[str, int, int]]:
    """Refine `while i < bound; i = i + 1` even when bound is symbolic.

    The `<` guard itself proves the in-loop value of `i` is at most
    INT64_MAX - 1, so the step-one increment cannot overflow. The shape is
    deliberately narrow and rejects loops with any additional counter write.
    """
    if not isinstance(node.cond, A.BinaryOp) or node.cond.op not in {"<", ">"}:
        return None
    if not isinstance(node.cond.left, A.Variable):
        return None
    counter = node.cond.left.name
    delta = _top_level_counter_delta(node.body, counter)
    if (node.cond.op == "<" and delta != 1) or (node.cond.op == ">" and delta != -1):
        return None
    if _assignment_count_for_var(node.body, counter) != 1:
        return None
    current = scope.get(counter)
    if current is None and delta == 1:
        return None
    int64_max = (1 << 63) - 1
    int64_min = -(1 << 63)
    low = int(current.low) if current is not None else int64_min
    high = int(current.high) if current is not None else int64_max
    if delta == 1:
        high = min(high, int64_max - 1)
    else:
        low = max(low, int64_min + 1)
    if low > high:
        return None
    return counter, low, high


def _while_counter_bound(
    node: A.While,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
) -> Tuple[Optional[str], Optional[int], int]:
    if not isinstance(node.cond, A.BinaryOp):
        return None, None, 0
    if node.cond.op not in {"<", "<="}:
        return None, None, 0
    if not isinstance(node.cond.left, A.Variable):
        return None, None, 0

    counter = node.cond.left.name
    bound_rng = facts._expr_interval(node.cond.right, func_scope, dict(scope))
    if bound_rng is None or bound_rng.low != bound_rng.high:
        return None, None, 0
    bound = bound_rng.low - 1 if node.cond.op == "<" else bound_rng.low

    step = _top_level_counter_step(node.body, counter)
    if step <= 0:
        return None, None, 0

    return counter, bound, step


def _assignment_count_for_var(nodes: Iterable[A.ASTNode], var_name: str) -> int:
    count = 0
    for node in nodes:
        count += _assignment_count_in_node(node, var_name)
    return count


def _assignment_count_in_node(node: A.ASTNode, var_name: str) -> int:
    count = 0
    if isinstance(node, A.Assign) and node.var_name == var_name:
        count += 1
    for child in _iter_ast_children(node):
        count += _assignment_count_in_node(child, var_name)
    return count


def _iter_ast_children(node: A.ASTNode) -> Iterable[A.ASTNode]:
    for value in vars(node).values():
        if isinstance(value, A.ASTNode):
            yield value
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, A.ASTNode):
                    yield item


def _top_level_counter_step(body: Iterable[A.ASTNode], var_name: str) -> int:
    delta = _top_level_counter_delta(body, var_name)
    return delta if delta > 0 else 0


def _top_level_counter_delta(body: Iterable[A.ASTNode], var_name: str) -> int:
    seen = 0
    delta = 0
    for stmt in body:
        if not isinstance(stmt, A.Assign) or stmt.var_name != var_name:
            continue
        seen += 1
        if seen > 1:
            return 0
        if not isinstance(stmt.value, A.BinaryOp) or stmt.value.op not in {"+", "-"}:
            return 0
        if (
            not isinstance(stmt.value.left, A.Variable)
            or stmt.value.left.name != var_name
        ):
            return 0
        if not isinstance(stmt.value.right, A.Number) or not isinstance(
            stmt.value.right.value, int
        ):
            return 0
        magnitude = int(stmt.value.right.value)
        if magnitude <= 0:
            return 0
        delta = magnitude if stmt.value.op == "+" else -magnitude
    return delta if seen == 1 else 0


def _nested_reduction_budget(
    body: Iterable[A.ASTNode],
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
) -> Tuple[Optional[str], Optional[int]]:
    range_bounds: Dict[str, Tuple[int, int]] = {}
    for stmt in body:
        if not isinstance(stmt, A.RangeVarDecl):
            continue
        low = facts._expr_interval(stmt.range_type.low, func_scope, dict(scope))
        high = facts._expr_interval(stmt.range_type.high, func_scope, dict(scope))
        if low is None or high is None or low.low != low.high or high.low != high.high:
            continue
        lo = low.low
        hi = high.low - 1 if stmt.range_type.exclusive else high.low
        if lo <= hi:
            range_bounds[stmt.var_name] = (lo, hi)

    for stmt in body:
        if not isinstance(stmt, A.While):
            continue
        if not (isinstance(stmt.cond, A.Bool) and bool(stmt.cond.value)):
            continue
        if len(stmt.body) != 3:
            continue
        add_stmt, break_stmt, step_stmt = stmt.body
        if not isinstance(add_stmt, A.Assign):
            continue
        if not isinstance(add_stmt.value, A.BinaryOp) or add_stmt.value.op != "+":
            continue
        if not (
            isinstance(add_stmt.value.left, A.Variable)
            and add_stmt.value.left.name == add_stmt.var_name
        ):
            continue
        rhs = add_stmt.value.right
        if not isinstance(rhs, A.ArrayAccess):
            continue
        if not isinstance(rhs.array, A.Variable) or not isinstance(
            rhs.index, A.Variable
        ):
            continue
        idx_name = rhs.index.name
        if idx_name not in range_bounds:
            continue
        idx_lo, idx_hi = range_bounds[idx_name]

        if not isinstance(break_stmt, A.If) or break_stmt.else_body:
            continue
        if len(break_stmt.then_body) != 1 or not isinstance(
            break_stmt.then_body[0], A.Break
        ):
            continue
        if not isinstance(break_stmt.cond, A.BinaryOp) or break_stmt.cond.op != "==":
            continue
        if (
            not isinstance(break_stmt.cond.left, A.Variable)
            or break_stmt.cond.left.name != idx_name
        ):
            continue
        if not isinstance(break_stmt.cond.right, A.Number) or not isinstance(
            break_stmt.cond.right.value, int
        ):
            continue
        if int(break_stmt.cond.right.value) != idx_hi:
            continue

        if not isinstance(step_stmt, A.Assign) or step_stmt.var_name != idx_name:
            continue
        if not isinstance(step_stmt.value, A.BinaryOp) or step_stmt.value.op != "+":
            continue
        if (
            not isinstance(step_stmt.value.left, A.Variable)
            or step_stmt.value.left.name != idx_name
        ):
            continue
        if (
            not isinstance(step_stmt.value.right, A.Number)
            or int(step_stmt.value.right.value) <= 0
        ):
            continue
        idx_step = int(step_stmt.value.right.value)

        arr_info = facts.get_array_info(rhs.array.name, func_scope)
        if arr_info is None:
            continue
        elem_rng, arr_len = arr_info
        if elem_rng.low < 0:
            continue
        if idx_lo < 0 or idx_hi >= arr_len:
            continue

        inner_iters = ((idx_hi - idx_lo) // idx_step) + 1
        if inner_iters <= 0:
            continue
        budget = inner_iters * elem_rng.high
        return add_stmt.var_name, budget

    return None, None


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


def _self_reduction_budget(
    body: Iterable[A.ASTNode],
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
) -> Tuple[Optional[str], Optional[int]]:
    body_list = list(body)
    if len(body_list) != 3:
        return None, None
    add_stmt, break_stmt, step_stmt = body_list
    if not isinstance(add_stmt, A.Assign):
        return None, None
    if not isinstance(add_stmt.value, A.BinaryOp) or add_stmt.value.op != "+":
        return None, None
    if not (
        isinstance(add_stmt.value.left, A.Variable)
        and add_stmt.value.left.name == add_stmt.var_name
    ):
        return None, None
    rhs = add_stmt.value.right
    if not isinstance(rhs, A.ArrayAccess):
        return None, None
    if not isinstance(rhs.array, A.Variable) or not isinstance(rhs.index, A.Variable):
        return None, None
    idx_name = rhs.index.name
    idx_rng = scope.get(idx_name)
    if idx_rng is None:
        return None, None
    idx_lo = int(idx_rng.low)
    idx_hi = int(idx_rng.high)
    if idx_lo > idx_hi:
        return None, None

    if not _is_break_guard_if(break_stmt):
        return None, None
    if not isinstance(break_stmt.cond, A.BinaryOp) or break_stmt.cond.op != "==":
        return None, None
    if (
        not isinstance(break_stmt.cond.left, A.Variable)
        or break_stmt.cond.left.name != idx_name
    ):
        return None, None
    if not isinstance(break_stmt.cond.right, A.Number) or not isinstance(
        break_stmt.cond.right.value, int
    ):
        return None, None
    limit = int(break_stmt.cond.right.value)
    if limit != idx_hi:
        return None, None

    if not isinstance(step_stmt, A.Assign) or step_stmt.var_name != idx_name:
        return None, None
    if not isinstance(step_stmt.value, A.BinaryOp) or step_stmt.value.op != "+":
        return None, None
    if (
        not isinstance(step_stmt.value.left, A.Variable)
        or step_stmt.value.left.name != idx_name
    ):
        return None, None
    if (
        not isinstance(step_stmt.value.right, A.Number)
        or int(step_stmt.value.right.value) <= 0
    ):
        return None, None
    step = int(step_stmt.value.right.value)

    arr_info = facts.get_array_info(rhs.array.name, func_scope)
    if arr_info is None:
        return None, None
    elem_rng, arr_len = arr_info
    if elem_rng.low < 0:
        return None, None
    if idx_lo < 0 or idx_hi >= arr_len:
        return None, None

    inner_iters = ((idx_hi - idx_lo) // step) + 1
    if inner_iters <= 0:
        return None, None
    return add_stmt.var_name, inner_iters * elem_rng.high
