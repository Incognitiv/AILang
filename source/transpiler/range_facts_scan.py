from __future__ import annotations

from parser import ast as A
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .range_facts_loop_patterns import (
    derive_specialized_while_ranges,
    is_branch_heavy_loop_body,
    while_true_break_guard_bounds,
)
from .range_facts_protocol import (
    _derive_modulo_assignment_ranges,
    _derive_protocol_loop_ranges,
    _mark_symbolic_guarded_char_at_calls,
)
from .range_facts_utils import (
    _body_exits_current_path,
    _drop_relations_for_var,
    _refine_scope_for_condition,
    _relations_with_condition,
    _remember_or_clear_string_info,
    _remember_or_clear_strlen_var,
)


def _dict_literal_ranges(
    expr: Optional[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(expr, A.DictLit):
        return None
    values: Dict[str, Any] = {}
    for key_expr, value_expr in expr.pairs:
        if not isinstance(key_expr, A.StringLit):
            return None
        value_rng = facts._expr_interval(value_expr, func_scope, dict(scope))
        if value_rng is not None:
            values[key_expr.value] = value_rng
    return values


def _remember_or_clear_dict_info(
    facts: Any,
    func_scope: Optional[str],
    var_name: str,
    expr: Optional[A.ASTNode],
    scope: Dict[str, Any],
) -> None:
    values = _dict_literal_ranges(expr, func_scope, facts, scope)
    if values is None:
        facts.clear_dict_value_info(var_name, func_scope)
    else:
        facts.set_dict_value_infos(var_name, values, func_scope)


def scan_if(
    analyzer: Any,
    node: A.If,
    func_scope: Optional[str],
    facts: Any,
    *,
    interval_ctor: Callable[[int, int], Any],
) -> None:
    base = dict(facts.scope_ranges.get(func_scope, {}))
    base_relations = set(facts.scope_relations.get(func_scope, set()))
    then_scope = dict(base)
    else_scope = dict(base)
    then_relations = _relations_with_condition(base_relations, node.cond, truthy=True)
    else_relations = _relations_with_condition(base_relations, node.cond, truthy=False)
    _refine_scope_for_condition(
        node.cond, then_scope, truthy=True, interval_ctor=interval_ctor
    )
    _refine_scope_for_condition(
        node.cond, else_scope, truthy=False, interval_ctor=interval_ctor
    )
    then_exits = _body_exits_current_path(node.then_body)
    else_exits = _body_exits_current_path(node.else_body or [])

    if then_exits and not node.else_body:
        facts.scope_ranges[func_scope] = then_scope
        facts.scope_relations[func_scope] = then_relations
        for stmt in node.then_body:
            analyzer._scan_node(stmt, func_scope, facts)
        facts.scope_ranges[func_scope] = else_scope
        facts.scope_relations[func_scope] = else_relations
        return

    if then_exits and else_exits:
        facts.scope_ranges[func_scope] = then_scope
        facts.scope_relations[func_scope] = then_relations
        for stmt in node.then_body:
            analyzer._scan_node(stmt, func_scope, facts)
        facts.scope_ranges[func_scope] = else_scope
        facts.scope_relations[func_scope] = else_relations
        for stmt in node.else_body or []:
            analyzer._scan_node(stmt, func_scope, facts)
        facts.scope_ranges[func_scope] = {}
        facts.scope_relations[func_scope] = set()
        return

    if then_exits:
        facts.scope_ranges[func_scope] = then_scope
        facts.scope_relations[func_scope] = then_relations
        for stmt in node.then_body:
            analyzer._scan_node(stmt, func_scope, facts)
        facts.scope_ranges[func_scope] = else_scope
        facts.scope_relations[func_scope] = else_relations
        for stmt in node.else_body or []:
            analyzer._scan_node(stmt, func_scope, facts)
        return

    if else_exits:
        facts.scope_ranges[func_scope] = else_scope
        facts.scope_relations[func_scope] = else_relations
        for stmt in node.else_body or []:
            analyzer._scan_node(stmt, func_scope, facts)
        facts.scope_ranges[func_scope] = then_scope
        facts.scope_relations[func_scope] = then_relations
        for stmt in node.then_body:
            analyzer._scan_node(stmt, func_scope, facts)
        return

    facts.scope_ranges[func_scope] = then_scope
    facts.scope_relations[func_scope] = then_relations
    for stmt in node.then_body:
        analyzer._scan_node(stmt, func_scope, facts)
    then_scope = dict(facts.scope_ranges.get(func_scope, {}))
    facts.scope_ranges[func_scope] = else_scope
    facts.scope_relations[func_scope] = else_relations
    for stmt in node.else_body or []:
        analyzer._scan_node(stmt, func_scope, facts)
    else_scope = dict(facts.scope_ranges.get(func_scope, {}))
    merged: Dict[str, Any] = {}
    for name in set(then_scope) | set(else_scope):
        left = then_scope.get(name)
        right = else_scope.get(name)
        if left is None or right is None:
            facts.set_unknown_reason(name, func_scope, "branch_unknown")
            facts.clear_nonnegative_var(func_scope, name)
            continue
        merged[name] = left.union(right)
        facts.clear_unknown_reason(name, func_scope)
        if merged[name].low >= 0:
            facts.mark_nonnegative_var(func_scope, name)
        else:
            facts.clear_nonnegative_var(func_scope, name)
    facts.scope_ranges[func_scope] = merged
    facts.scope_relations[func_scope] = base_relations


def scan_function(analyzer: Any, node: A.Function, facts: Any) -> None:
    scope = node.name
    facts.scope_ranges[scope] = {}
    facts.call_hint_params[scope] = set()
    call_hints = facts.call_arg_ranges.get(node.name, {})
    string_hints = facts.call_arg_string_infos.get(node.name, {})
    for pidx, param in enumerate(node.params or []):
        if not isinstance(param, tuple) or len(param) < 2:
            continue
        pname, ptype = param[0], param[1]
        rng = analyzer._range_from_type_name(ptype, facts)
        if rng is None:
            rng = call_hints.get(pidx)
            if rng is not None:
                facts.call_hint_params[scope].add(pname)
        if rng is not None:
            facts.scope_ranges[scope][pname] = rng
        info = string_hints.get(pidx)
        if info is not None:
            facts.set_string_info(scope, pname, info)
    for stmt in node.body:
        analyzer._scan_node(stmt, scope, facts)


def scan_node(
    analyzer: Any,
    node: A.ASTNode,
    func_scope: Optional[str],
    facts: Any,
    *,
    interval_ctor: Callable[[int, int], Any],
) -> None:
    scope = facts.scope_ranges.setdefault(func_scope, {})
    scope_before = dict(scope)
    if isinstance(node, A.Function):
        analyzer._scan_function(node, facts)
        return
    if isinstance(node, A.RangeVarDecl):
        if analyzer._expr_contains_unknown_side_effect(node.init_value):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.init_value, func_scope, facts)
        analyzer._observe_calls_in_expr(
            node.init_value, func_scope, facts, scope_before
        )
        rng = analyzer._range_from_range_type(node.range_type, facts, func_scope)
        if rng is not None:
            scope[node.var_name] = rng
            facts.lock_var_range(node.var_name, func_scope)
            facts.clear_unknown_reason(node.var_name, func_scope)
            facts.clear_loop_reason(node.var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, node.var_name)
            if rng.low >= 0:
                facts.mark_nonnegative_var(func_scope, node.var_name)
            else:
                facts.clear_nonnegative_var(func_scope, node.var_name)
        return
    if isinstance(node, A.VarDecl):
        if analyzer._expr_contains_unknown_side_effect(node.init_value):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.init_value, func_scope, facts)
        analyzer._observe_calls_in_expr(
            node.init_value, func_scope, facts, scope_before
        )
        alias_rng = analyzer._range_from_type_name(node.type_name, facts)
        init_rng = facts._expr_interval(node.init_value, func_scope, dict(scope))
        if alias_rng is not None:
            scope[node.var_name] = alias_rng
            facts.clear_unknown_reason(node.var_name, func_scope)
            facts.clear_loop_reason(node.var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, node.var_name)
            if alias_rng.low >= 0:
                facts.mark_nonnegative_var(func_scope, node.var_name)
            else:
                facts.clear_nonnegative_var(func_scope, node.var_name)
        elif init_rng is not None:
            scope[node.var_name] = init_rng
            facts.clear_unknown_reason(node.var_name, func_scope)
            facts.clear_loop_reason(node.var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, node.var_name)
            if init_rng.low >= 0:
                facts.mark_nonnegative_var(func_scope, node.var_name)
            else:
                facts.clear_nonnegative_var(func_scope, node.var_name)
        else:
            scope.pop(node.var_name, None)
            facts.set_unknown_reason(node.var_name, func_scope, "value_unknown")
            facts.clear_loop_reason(node.var_name, func_scope)
            facts.clear_nonnegative_var(func_scope, node.var_name)
            _drop_relations_for_var(facts, func_scope, node.var_name)
        arr_info = analyzer._infer_array_info(
            node.init_value, func_scope, facts, dict(scope)
        )
        if arr_info is not None:
            facts.set_array_info(node.var_name, arr_info[0], arr_info[1], func_scope)
        else:
            facts.clear_array_info(node.var_name, func_scope)
        _remember_or_clear_dict_info(
            facts, func_scope, node.var_name, node.init_value, dict(scope)
        )
        _remember_or_clear_strlen_var(facts, func_scope, node.var_name, node.init_value)
        _remember_or_clear_string_info(
            facts, func_scope, node.var_name, node.init_value, dict(scope)
        )
        return
    if isinstance(node, A.Assign):
        if analyzer._expr_contains_unknown_side_effect(node.value):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.value, func_scope, facts)
        analyzer._observe_calls_in_expr(node.value, func_scope, facts, scope_before)
        if node.var_name in facts.locked_ranges.get(func_scope, set()):
            return
        value_rng = facts._expr_interval(node.value, func_scope, dict(scope))
        if value_rng is None:
            scope.pop(node.var_name, None)
            facts.set_unknown_reason(node.var_name, func_scope, "value_unknown")
            facts.clear_loop_reason(node.var_name, func_scope)
            facts.clear_nonnegative_var(func_scope, node.var_name)
            _drop_relations_for_var(facts, func_scope, node.var_name)
        else:
            scope[node.var_name] = value_rng
            facts.clear_unknown_reason(node.var_name, func_scope)
            facts.clear_loop_reason(node.var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, node.var_name)
            if value_rng.low >= 0:
                facts.mark_nonnegative_var(func_scope, node.var_name)
            else:
                facts.clear_nonnegative_var(func_scope, node.var_name)
        arr_info = analyzer._infer_array_info(
            node.value, func_scope, facts, dict(scope)
        )
        if arr_info is not None:
            facts.set_array_info(node.var_name, arr_info[0], arr_info[1], func_scope)
        else:
            facts.clear_array_info(node.var_name, func_scope)
        _remember_or_clear_dict_info(
            facts, func_scope, node.var_name, node.value, dict(scope)
        )
        _remember_or_clear_strlen_var(facts, func_scope, node.var_name, node.value)
        _remember_or_clear_string_info(
            facts, func_scope, node.var_name, node.value, dict(scope)
        )
        return
    if isinstance(node, A.TupleAssign):
        for expr in node.values:
            if analyzer._expr_contains_unknown_side_effect(expr):
                analyzer._invalidate_for_side_effect(func_scope, facts)
                break
        for expr in node.values:
            analyzer._capture_expr_tree(expr, func_scope, facts)
            analyzer._observe_calls_in_expr(expr, func_scope, facts, scope_before)
        for var_name in node.var_names:
            if var_name in facts.locked_ranges.get(func_scope, set()):
                continue
            scope.pop(var_name, None)
            facts.set_unknown_reason(var_name, func_scope, "value_unknown")
            facts.clear_array_info(var_name, func_scope)
            facts.clear_dict_value_info(var_name, func_scope)
            facts.clear_string_info(func_scope, var_name)
            facts.clear_string_len_var(func_scope, var_name)
            facts.clear_loop_reason(var_name, func_scope)
            facts.clear_nonnegative_var(func_scope, var_name)
            _drop_relations_for_var(facts, func_scope, var_name)
        return
    if isinstance(node, A.DictAssign):
        for expr in (node.dict_expr, node.key_expr, node.value_expr):
            if analyzer._expr_contains_unknown_side_effect(expr):
                analyzer._invalidate_for_side_effect(func_scope, facts)
                break
        for expr in (node.dict_expr, node.key_expr, node.value_expr):
            analyzer._capture_expr_tree(expr, func_scope, facts)
            analyzer._observe_calls_in_expr(expr, func_scope, facts, scope_before)
        if isinstance(node.dict_expr, A.Variable):
            dict_name = node.dict_expr.name
            if isinstance(node.key_expr, A.StringLit):
                value_rng = facts._expr_interval(
                    node.value_expr, func_scope, dict(scope)
                )
                if value_rng is None:
                    facts.clear_dict_value_info(
                        dict_name, func_scope, node.key_expr.value
                    )
                else:
                    facts.set_dict_value_info(
                        dict_name, node.key_expr.value, value_rng, func_scope
                    )
            else:
                facts.clear_dict_value_info(dict_name, func_scope)
        return
    if isinstance(node, A.If):
        if analyzer._expr_contains_unknown_side_effect(node.cond):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.cond, func_scope, facts)
        analyzer._observe_calls_in_expr(node.cond, func_scope, facts, scope_before)
        analyzer._scan_if(node, func_scope, facts, interval_ctor=interval_ctor)
        return
    if isinstance(node, A.While):
        if analyzer._expr_contains_unknown_side_effect(node.cond):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.cond, func_scope, facts)
        analyzer._observe_calls_in_expr(node.cond, func_scope, facts, scope_before)
        if analyzer._expr_contains_unknown_side_effect(node.max_iterations):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.max_iterations, func_scope, facts)
        analyzer._observe_calls_in_expr(
            node.max_iterations, func_scope, facts, scope_before
        )
        raw_refinements, ignore_first_break_guard = while_true_break_guard_bounds(
            node,
            func_scope=func_scope,
            facts=facts,
            scope=dict(scope),
        )
        loop_scope_refinements = {
            name: interval_ctor(low, high)
            for name, (low, high) in raw_refinements.items()
        }
        loop_scope_reasons: Dict[str, str] = {
            name: "loop_guard_proven" for name in loop_scope_refinements
        }
        specialized_refinements, preserve_assigned = derive_specialized_while_ranges(
            node,
            func_scope=func_scope,
            facts=facts,
            scope=dict(scope),
        )
        for name, (low, high) in specialized_refinements.items():
            existing = loop_scope_refinements.get(name)
            if existing is None:
                loop_scope_refinements[name] = interval_ctor(low, high)
            else:
                loop_scope_refinements[name] = interval_ctor(
                    max(existing.low, low), min(existing.high, high)
                )
        protocol_scope = dict(scope)
        for name, rng in loop_scope_refinements.items():
            protocol_scope[name] = rng
        protocol_refinements, protocol_preserve = _derive_protocol_loop_ranges(
            analyzer,
            node,
            func_scope,
            facts,
            protocol_scope,
        )
        preserve_assigned.update(protocol_preserve)
        modulo_refinements, modulo_preserve = _derive_modulo_assignment_ranges(
            analyzer,
            node.body,
            func_scope,
            facts,
            protocol_scope,
        )
        preserve_assigned.update(modulo_preserve)
        _mark_symbolic_guarded_char_at_calls(
            analyzer,
            node.body,
            func_scope,
            facts,
            dict(scope),
            node.cond,
        )
        counter_name = (
            node.cond.left.name
            if isinstance(node.cond, A.BinaryOp)
            and isinstance(node.cond.left, A.Variable)
            else ""
        )
        for name, (low, high) in specialized_refinements.items():
            existing = loop_scope_refinements.get(name)
            if existing is None:
                loop_scope_refinements[name] = interval_ctor(low, high)
            else:
                loop_scope_refinements[name] = interval_ctor(
                    max(existing.low, low), min(existing.high, high)
                )
            if name == counter_name:
                loop_scope_reasons[name] = "loop_counter_proven"
            else:
                loop_scope_reasons[name] = "loop_accumulator_proven"
        for name, (low, high) in protocol_refinements.items():
            existing = loop_scope_refinements.get(name)
            if existing is None:
                loop_scope_refinements[name] = interval_ctor(low, high)
            else:
                loop_scope_refinements[name] = interval_ctor(
                    max(existing.low, low), min(existing.high, high)
                )
            loop_scope_reasons[name] = "protocol_parser_proven"
        for name, (low, high) in modulo_refinements.items():
            existing = loop_scope_refinements.get(name)
            if existing is None:
                loop_scope_refinements[name] = interval_ctor(low, high)
            else:
                loop_scope_refinements[name] = interval_ctor(
                    max(existing.low, low), min(existing.high, high)
                )
            loop_scope_reasons[name] = "protocol_modulo_proven"
        analyzer._scan_loop(
            node.body,
            func_scope,
            facts,
            loop_scope_refinements=loop_scope_refinements,
            loop_scope_reasons=loop_scope_reasons,
            ignore_first_break_guard=ignore_first_break_guard,
            preserve_assigned=preserve_assigned,
        )
        return
    if isinstance(node, A.For):
        if node.init is not None:
            analyzer._scan_node(node.init, func_scope, facts)
        if analyzer._expr_contains_unknown_side_effect(node.cond):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.cond, func_scope, facts)
        analyzer._observe_calls_in_expr(node.cond, func_scope, facts, scope_before)
        if analyzer._expr_contains_unknown_side_effect(node.max_iterations):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.max_iterations, func_scope, facts)
        analyzer._observe_calls_in_expr(
            node.max_iterations, func_scope, facts, scope_before
        )
        analyzer._scan_loop(
            node.body,
            func_scope,
            facts,
            extra_assigned_nodes=[node.step] if node.step is not None else [],
        )
        if node.step is not None:
            analyzer._scan_node(node.step, func_scope, facts)
        return
    if isinstance(node, A.Return):
        if analyzer._expr_contains_unknown_side_effect(node.value):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.value, func_scope, facts)
        analyzer._observe_calls_in_expr(node.value, func_scope, facts, scope_before)
        if node.value is not None and func_scope is not None:
            ret_rng = facts._expr_interval(node.value, func_scope, dict(scope))
            if ret_rng is not None:
                facts.observe_function_return_interval(func_scope, ret_rng)
        return
    if isinstance(node, A.Assert):
        if analyzer._expr_contains_unknown_side_effect(node.condition):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.condition, func_scope, facts)
        analyzer._observe_calls_in_expr(node.condition, func_scope, facts, scope_before)
        if analyzer._expr_contains_unknown_side_effect(node.message):
            analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node.message, func_scope, facts)
        analyzer._observe_calls_in_expr(node.message, func_scope, facts, scope_before)
        return
    if analyzer._node_is_unknown_side_effect_barrier(node):
        analyzer._invalidate_for_side_effect(func_scope, facts)
        analyzer._capture_expr_tree(node, func_scope, facts)
        analyzer._observe_calls_in_expr(node, func_scope, facts, scope_before)
        return


def scan_loop(
    analyzer: Any,
    body: List[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    *,
    extra_assigned_nodes: Optional[Iterable[A.ASTNode]] = None,
    loop_scope_refinements: Optional[Dict[str, Any]] = None,
    loop_scope_reasons: Optional[Dict[str, str]] = None,
    ignore_first_break_guard: bool = False,
    preserve_assigned: Optional[Set[str]] = None,
) -> None:
    base = dict(facts.scope_ranges.get(func_scope, {}))
    assigned = analyzer._collect_assigned_vars(body)
    if extra_assigned_nodes:
        assigned |= analyzer._collect_assigned_vars(extra_assigned_nodes)

    loop_scope = dict(base)
    if loop_scope_refinements:
        for name, rng in loop_scope_refinements.items():
            loop_scope[name] = rng
            facts.clear_unknown_reason(name, func_scope)
            if loop_scope_reasons and name in loop_scope_reasons:
                facts.set_loop_reason(name, func_scope, loop_scope_reasons[name])
            else:
                facts.clear_loop_reason(name, func_scope)
    preserved = preserve_assigned or set()
    locked = facts.locked_ranges.get(func_scope, set())
    invalidated_names: Set[str] = set()
    branch_heavy = is_branch_heavy_loop_body(
        body,
        ignore_first_break_guard=ignore_first_break_guard,
        walk_ast=analyzer._walk_ast,
    )
    side_effect_unknown = analyzer._contains_unknown_side_effect_call(body)
    if branch_heavy or side_effect_unknown:
        reason = "loop_branch_unknown" if branch_heavy else "side_effect_unknown"
        for var_name in list(loop_scope.keys()):
            if var_name in preserved and reason != "side_effect_unknown":
                continue
            if var_name in locked and reason != "side_effect_unknown":
                continue
            loop_scope.pop(var_name, None)
            facts.set_unknown_reason(var_name, func_scope, reason)
            facts.clear_loop_reason(var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, var_name)
            invalidated_names.add(var_name)
    for var_name in assigned:
        if var_name in preserved:
            continue
        if var_name not in locked:
            loop_scope.pop(var_name, None)
            facts.set_unknown_reason(var_name, func_scope, "loop_unknown")
            facts.clear_loop_reason(var_name, func_scope)
            _drop_relations_for_var(facts, func_scope, var_name)
            invalidated_names.add(var_name)

    facts.scope_ranges[func_scope] = loop_scope
    for stmt in body:
        analyzer._scan_node(stmt, func_scope, facts)
    body_scope = dict(facts.scope_ranges.get(func_scope, {}))
    merged: Dict[str, Any] = {}
    for name in set(base) | set(body_scope):
        if name in invalidated_names:
            continue
        left = base.get(name)
        right = body_scope.get(name)
        if left is None or right is None:
            continue
        merged[name] = left.union(right)
        facts.clear_unknown_reason(name, func_scope)
        # Only clear loop-reason tags for variables managed by this loop.
        # This avoids clobbering outer-loop reason tags when scanning nested loops.
        if name in assigned or (loop_scope_reasons and name in loop_scope_reasons):
            facts.clear_loop_reason(name, func_scope)
    facts.scope_ranges[func_scope] = merged
    facts.scope_relations[func_scope] = set()
