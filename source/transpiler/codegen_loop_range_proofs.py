"""Counted-loop range proofs for the C backend."""

from __future__ import annotations

from parser import ast as A
from typing import Dict, Iterable, List, Optional, Set, Tuple

from transpiler.array_literal_hints import get_array_literal_values
from transpiler.codegen_int_ranges import (
    INT64_MAX,
    FieldKey,
    IntRange,
    _clamp_if_pattern,
    expr_int_range,
    remember_string_length_range,
)


def derive_counted_loop_ranges(self, node: A.While) -> Set[str]:
    return _derive_counted_loop_ranges(self, node)


def _derive_counted_loop_ranges(self, node: A.While) -> Set[str]:
    counter, bound, step = _while_counter_bound(self, node)
    if counter is None or bound is None or step <= 0:
        return set()
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    current = ranges.get(counter)
    if current is None or current[0] > bound:
        return set()
    trip_count = ((bound - current[0]) // step) + 1
    if trip_count <= 0:
        return set()
    preserved = {counter}
    ranges[counter] = (current[0], bound)
    simulated_fields = dict(getattr(self, "_codegen_field_int_ranges", {}))
    acc_name: Optional[str] = None
    per_iter_budget: Optional[int] = None
    exact = _exact_indexed_reduction_details(
        self, node, counter, current[0], bound, step
    )
    exact_name, total_budget, max_term = exact
    if exact_name is not None:
        acc_current = ranges.get(exact_name)
        if (
            acc_current is not None
            and acc_current[0] >= 0
            and total_budget is not None
            and max_term is not None
        ):
            ranges[exact_name] = (
                acc_current[0],
                max(acc_current[1], acc_current[1] + total_budget - max_term),
            )
            preserved.add(exact_name)
    else:
        acc_name, per_iter_budget = _loop_accumulator_budget(
            self, node.body, counter, simulated_fields
        )
    clamped_ranges = _loop_clamped_accumulator_ranges(self, node.body, counter)
    if clamped_ranges:
        ranges.update(clamped_ranges)
        preserved.update(clamped_ranges.keys())
    if acc_name is not None and per_iter_budget is not None:
        acc_current = ranges.get(acc_name)
        if acc_current is not None and acc_current[0] >= 0 and per_iter_budget >= 0:
            total_high = acc_current[1] + (trip_count * per_iter_budget)
            if total_high <= INT64_MAX:
                # Use the pre-update high watermark inside the loop body.  The
                # assignment itself can reach total_high; it must not start from it.
                ranges[acc_name] = (
                    acc_current[0],
                    max(acc_current[1], total_high - per_iter_budget),
                )
                preserved.add(acc_name)
    self._codegen_int_ranges = ranges
    self._codegen_field_int_ranges = simulated_fields
    return preserved


def _loop_clamped_accumulator_ranges(
    self, body: Iterable[A.ASTNode], counter_name: str
) -> Dict[str, IntRange]:
    clamp_limits: Dict[str, int] = {}
    update_exprs: Dict[str, A.ASTNode] = {}
    for stmt in body:
        clamp = _clamp_if_pattern(stmt)
        if clamp is not None:
            var_name, limit = clamp
            clamp_limits[var_name] = limit
            continue
        if isinstance(stmt, A.Assign) and stmt.var_name != counter_name:
            if stmt.var_name in update_exprs:
                return {}
            update_exprs[stmt.var_name] = stmt.value
    if not clamp_limits or set(clamp_limits) != set(update_exprs):
        return {}

    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    refined: Dict[str, IntRange] = {}
    for var_name, limit in clamp_limits.items():
        current = ranges.get(var_name)
        if current is None or current[0] < 0 or current[1] > limit:
            return {}
        refined[var_name] = (0, limit)

    saved = dict(ranges)
    try:
        ranges.update(refined)
        self._codegen_int_ranges = ranges
        for var_name, limit in clamp_limits.items():
            update_range = expr_int_range(self, update_exprs[var_name])
            if (
                update_range is None
                or update_range[0] < 0
                or update_range[1] > (limit * 2)
            ):
                return {}
    finally:
        self._codegen_int_ranges = saved
    return refined


def _while_counter_bound(
    self, node: A.While
) -> Tuple[Optional[str], Optional[int], int]:
    if not isinstance(node.cond, A.BinaryOp):
        return None, None, 0
    if node.cond.op not in {"<", "<="}:
        return None, None, 0
    if not isinstance(node.cond.left, A.Variable):
        return None, None, 0
    counter = node.cond.left.name
    bound_rng = expr_int_range(self, node.cond.right)
    if bound_rng is None:
        return None, None, 0
    # A range-typed loop bound is still usable for proof: the upper end
    # gives the worst-case trip count, so preserving derived ranges remains
    # conservative.
    bound_limit = bound_rng[1]
    bound = bound_limit - 1 if node.cond.op == "<" else bound_limit
    step = _top_level_counter_step(node.body, counter)
    return counter, bound, step


def _top_level_counter_step(body: Iterable[A.ASTNode], var_name: str) -> int:
    seen = 0
    delta = 0
    for stmt in body:
        if not isinstance(stmt, A.Assign) or stmt.var_name != var_name:
            continue
        seen += 1
        if seen > 1:
            return 0
        delta = _counter_delta(stmt.value, var_name)
        if delta <= 0:
            return 0
    return delta


def _counter_delta(expr: A.ASTNode, var_name: str) -> int:
    if not isinstance(expr, A.BinaryOp) or expr.op not in {"+", "-"}:
        return 0
    if not isinstance(expr.left, A.Variable) or expr.left.name != var_name:
        return 0
    if not isinstance(expr.right, A.Number) or not isinstance(expr.right.value, int):
        return 0
    literal = int(expr.right.value)
    return literal if expr.op == "+" else -literal


def _loop_accumulator_budget(
    self,
    body: Iterable[A.ASTNode],
    counter_name: str,
    field_ranges: Dict[FieldKey, IntRange],
) -> Tuple[Optional[str], Optional[int]]:
    acc_name: Optional[str] = None
    budget = 0
    found_growth = False
    saved_ranges = getattr(self, "_codegen_int_ranges", {})
    saved_string_lengths = getattr(self, "_codegen_string_length_ranges", {})
    local_ranges = dict(saved_ranges)
    local_string_lengths = dict(saved_string_lengths)
    try:
        self._codegen_int_ranges = local_ranges
        self._codegen_string_length_ranges = local_string_lengths
        for stmt in body:
            if isinstance(stmt, A.RangeVarDecl):
                _simulate_local_decl_range(self, stmt.var_name, stmt.init_value)
                continue
            if isinstance(stmt, A.VarDecl):
                _simulate_local_decl_range(self, stmt.var_name, stmt.init_value)
                continue
            if isinstance(stmt, A.FieldAssign):
                _simulate_field_assign_range(self, stmt, field_ranges)
                continue
            if isinstance(stmt, A.While):
                nested_name, nested_budget = _while_accumulator_budget(
                    self, stmt, field_ranges
                )
                if nested_name is None or nested_budget is None:
                    return None, None
                if acc_name is None:
                    acc_name = nested_name
                if nested_name != acc_name:
                    return None, None
                budget += nested_budget
                found_growth = True
                continue
            if not isinstance(stmt, A.Assign):
                continue
            if stmt.var_name == counter_name:
                _simulate_local_decl_range(self, stmt.var_name, stmt.value)
                continue
            terms = _self_accumulator_terms(stmt.var_name, stmt.value)
            if terms is None:
                if stmt.var_name == acc_name:
                    return None, None
                _simulate_local_decl_range(self, stmt.var_name, stmt.value)
                continue
            if acc_name is None:
                acc_name = stmt.var_name
            if stmt.var_name != acc_name:
                return None, None
            for term in terms:
                rng = _expr_int_range_with_fields(self, term, field_ranges)
                if rng is None or rng[0] < 0:
                    return None, None
                budget += rng[1]
                found_growth = True
            _simulate_local_decl_range(self, stmt.var_name, stmt.value)
    finally:
        self._codegen_int_ranges = saved_ranges
        self._codegen_string_length_ranges = saved_string_lengths
    if acc_name is None or not found_growth:
        return None, None
    return acc_name, budget


def _simulate_local_decl_range(self, var_name: str, expr: Optional[A.ASTNode]) -> None:
    remember_string_length_range(self, var_name, expr)
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    if expr is None:
        ranges.pop(var_name, None)
        return
    rng = expr_int_range(self, expr)
    if rng is None:
        ranges.pop(var_name, None)
    else:
        ranges[var_name] = rng
    self._codegen_int_ranges = ranges


def _while_accumulator_budget(
    self, node: A.While, field_ranges: Dict[FieldKey, IntRange]
) -> Tuple[Optional[str], Optional[int]]:
    counter, bound, step = _while_counter_bound(self, node)
    if counter is None or bound is None or step <= 0:
        return None, None
    ranges: Dict[str, IntRange] = getattr(self, "_codegen_int_ranges", {})
    current = ranges.get(counter)
    if current is None or current[0] > bound:
        return None, None
    trip_count = ((bound - current[0]) // step) + 1
    if trip_count <= 0:
        return None, None
    exact = _exact_indexed_reduction_budget(
        self, node, counter, current[0], bound, step
    )
    if exact[0] is not None:
        return exact
    saved_ranges = dict(ranges)
    saved_string_lengths = dict(getattr(self, "_codegen_string_length_ranges", {}))
    try:
        ranges[counter] = (current[0], bound)
        self._codegen_int_ranges = ranges
        acc_name, per_iter_budget = _loop_accumulator_budget(
            self, node.body, counter, field_ranges
        )
    finally:
        self._codegen_int_ranges = saved_ranges
        self._codegen_string_length_ranges = saved_string_lengths
    if acc_name is None or per_iter_budget is None:
        return None, None
    return acc_name, per_iter_budget * trip_count


def _exact_indexed_reduction_budget(
    self, node: A.While, counter: str, start: int, bound: int, step: int
) -> Tuple[Optional[str], Optional[int]]:
    details = _exact_indexed_reduction_details(self, node, counter, start, bound, step)
    return details[0], details[1]


def _exact_indexed_reduction_details(
    self, node: A.While, counter: str, start: int, bound: int, step: int
) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    acc_name: Optional[str] = None
    add_term: Optional[A.ArrayAccess] = None
    array_name: Optional[str] = None
    for stmt in node.body:
        if isinstance(stmt, A.Assign) and stmt.var_name == counter:
            continue
        if not isinstance(stmt, A.Assign):
            return None, None, None
        terms = _self_accumulator_terms(stmt.var_name, stmt.value)
        if terms is None or len(terms) != 1:
            return None, None, None
        term = terms[0]
        if not (
            isinstance(term, A.ArrayAccess)
            and isinstance(term.array, A.Variable)
            and isinstance(term.index, A.Variable)
            and term.index.name == counter
        ):
            return None, None, None
        if acc_name is not None and stmt.var_name != acc_name:
            return None, None, None
        acc_name = stmt.var_name
        add_term = term
        array_name = term.array.name
    if acc_name is None or add_term is None or array_name is None:
        return None, None, None
    values = get_array_literal_values(self, array_name)
    if values is None or start < 0 or bound >= len(values):
        return None, None, None
    selected = values[start : bound + 1 : step]
    if not selected or any(value < 0 for value in selected):
        return None, None, None
    return acc_name, sum(selected), max(selected)


def _simulate_field_assign_range(
    self, stmt: A.FieldAssign, field_ranges: Dict[FieldKey, IntRange]
) -> None:
    if not isinstance(stmt.object_expr, A.Variable):
        return
    key = (stmt.object_expr.name, stmt.field_name)
    rng = _expr_int_range_with_fields(self, stmt.value, field_ranges)
    if rng is None:
        field_ranges.pop(key, None)
        return
    existing = field_ranges.get(key)
    field_ranges[key] = (
        rng
        if existing is None
        else (
            min(existing[0], rng[0]),
            max(existing[1], rng[1]),
        )
    )


def _self_accumulator_terms(
    var_name: str, expr: A.ASTNode
) -> Optional[List[A.ASTNode]]:
    terms = _flatten_add(expr)
    if not terms:
        return None
    first = terms[0]
    if not isinstance(first, A.Variable) or first.name != var_name:
        return None
    return terms[1:]


def _flatten_add(expr: A.ASTNode) -> List[A.ASTNode]:
    if isinstance(expr, A.BinaryOp) and expr.op == "+":
        return _flatten_add(expr.left) + _flatten_add(expr.right)
    return [expr]


def _expr_int_range_with_fields(
    self, expr: A.ASTNode, field_ranges: Dict[FieldKey, IntRange]
) -> Optional[IntRange]:
    saved = getattr(self, "_codegen_field_int_ranges", {})
    try:
        self._codegen_field_int_ranges = field_ranges
        return expr_int_range(self, expr)
    finally:
        self._codegen_field_int_ranges = saved
