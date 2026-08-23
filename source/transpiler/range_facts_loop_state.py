from __future__ import annotations

from parser import ast as A
from typing import Any, Dict, List, Optional, Tuple

from ast_access import arg_at

from .range_facts_types import Interval, StringInfo

DictState = Dict[str, Dict[str, Interval]]


def dict_state_from_facts(facts: Any, func_scope: Optional[str]) -> DictState:
    state: DictState = {}
    for scope_name in (None, func_scope):
        scoped = facts.dict_value_infos.get(scope_name, {})
        for name, values in scoped.items():
            merged = dict(state.get(name, {}))
            merged.update(values)
            state[name] = merged
    return state


def expr_interval_with_loop_state(
    expr: A.ASTNode,
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
    dict_state: DictState,
    transient_strings: Dict[str, StringInfo],
) -> Optional[Interval]:
    if isinstance(expr, A.Number) and isinstance(expr.value, int):
        return Interval(int(expr.value), int(expr.value))
    if isinstance(expr, A.Variable):
        return scope.get(expr.name)
    if isinstance(expr, A.Cast):
        return expr_interval_with_loop_state(
            expr.expr,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            dict_state=dict_state,
            transient_strings=transient_strings,
        )
    dict_key = literal_dict_key_access(expr)
    if dict_key is not None:
        dict_name, key = dict_key
        values = dict_state.get(dict_name)
        if values is not None and key in values:
            return values[key]
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
    if isinstance(expr, A.BinaryOp):
        left = expr_interval_with_loop_state(
            expr.left,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            dict_state=dict_state,
            transient_strings=transient_strings,
        )
        right = expr_interval_with_loop_state(
            expr.right,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            dict_state=dict_state,
            transient_strings=transient_strings,
        )
        if left is None or right is None:
            return None
        if expr.op == "+":
            return Interval(left.low + right.low, left.high + right.high)
        if expr.op == "-":
            return Interval(left.low - right.high, left.high - right.low)
        if expr.op == "*":
            vals = (
                left.low * right.low,
                left.low * right.high,
                left.high * right.low,
                left.high * right.high,
            )
            return Interval(min(vals), max(vals))
        if expr.op in {"%", "mod"} and left.low >= 0 and right.low > 0:
            return Interval(0, max(0, right.high - 1))
    return facts._expr_interval(expr, func_scope, dict(scope))


def literal_dict_key_access(expr: A.ASTNode) -> Optional[Tuple[str, str]]:
    if isinstance(expr, A.ArrayAccess):
        if isinstance(expr.array, A.Variable) and isinstance(expr.index, A.StringLit):
            return expr.array.name, expr.index.value
    if isinstance(expr, A.DictAccess):
        if isinstance(expr.dict_expr, A.Variable) and isinstance(
            expr.key_expr, A.StringLit
        ):
            return expr.dict_expr.name, expr.key_expr.value
    return None


def literal_dict_assignment(node: A.ASTNode) -> Optional[Tuple[str, str, A.ASTNode]]:
    if not isinstance(node, A.DictAssign):
        return None
    if not isinstance(node.dict_expr, A.Variable):
        return None
    if not isinstance(node.key_expr, A.StringLit):
        return None
    return node.dict_expr.name, node.key_expr.value, node.value_expr


def self_accumulator_growth_terms(
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
