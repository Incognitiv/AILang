"""Utility helpers extracted from RangeFactsAnalyzer."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from ast_access import arg_at


def collect_assigned_vars(analyzer: Any, nodes: Iterable[A.ASTNode]) -> Set[str]:
    assigned: Set[str] = set()
    for node in nodes:
        if isinstance(node, A.Assign):
            assigned.add(node.var_name)
        elif isinstance(node, A.VarDecl):
            assigned.add(node.var_name)
        elif isinstance(node, A.RangeVarDecl):
            assigned.add(node.var_name)
        elif isinstance(node, A.TupleAssign):
            assigned.update(node.var_names)
        elif isinstance(node, A.If):
            assigned |= analyzer._collect_assigned_vars(node.then_body)
            assigned |= analyzer._collect_assigned_vars(node.else_body or [])
        elif isinstance(node, A.While):
            assigned |= analyzer._collect_assigned_vars(node.body)
        elif isinstance(node, A.For):
            parts: List[A.ASTNode] = []
            if node.init is not None:
                parts.append(node.init)
            parts.extend(node.body)
            if node.step is not None:
                parts.append(node.step)
            assigned |= analyzer._collect_assigned_vars(parts)
    return assigned


def capture_expr_tree(
    analyzer: Any,
    expr: Optional[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
) -> None:
    if expr is None or not isinstance(expr, A.ASTNode):
        return
    scope_snapshot = dict(facts.scope_ranges.get(func_scope, {}))
    for node in analyzer._walk_ast(expr):
        facts.capture_expr_scope(node, func_scope, scope_snapshot)


def walk_ast(analyzer: Any, node: A.ASTNode) -> Iterable[A.ASTNode]:
    yield node
    for value in vars(node).values():
        yield from analyzer._iter_child_nodes(value)


def iter_child_nodes(analyzer: Any, value: object) -> Iterable[A.ASTNode]:
    if isinstance(value, A.ASTNode):
        yield from analyzer._walk_ast(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from analyzer._iter_child_nodes(item)
        return
    if isinstance(value, tuple):
        for item in value:
            yield from analyzer._iter_child_nodes(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from analyzer._iter_child_nodes(item)


def invalidate_non_locked_scope(
    analyzer: Any,
    func_scope: Optional[str],
    facts: Any,
    *,
    reason: str,
    include_locked: bool = False,
) -> None:
    scope = facts.scope_ranges.setdefault(func_scope, {})
    arrays = facts.array_infos.setdefault(func_scope, {})
    dicts = facts.dict_value_infos.setdefault(func_scope, {})
    strings = facts.string_infos.setdefault(func_scope, {})
    locked = facts.locked_ranges.get(func_scope, set())
    for name in list(scope.keys()):
        if name in locked and not include_locked:
            continue
        scope.pop(name, None)
        arrays.pop(name, None)
        dicts.pop(name, None)
        strings.pop(name, None)
        facts.clear_string_len_var(func_scope, name)
        facts.clear_nonnegative_var(func_scope, name)
        facts.set_unknown_reason(name, func_scope, reason)
        facts.clear_loop_reason(name, func_scope)
        _drop_relations_for_var(facts, func_scope, name)


def invalidate_for_side_effect(
    analyzer: Any, func_scope: Optional[str], facts: Any
) -> None:
    analyzer._invalidate_non_locked_scope(
        func_scope,
        facts,
        reason="side_effect_unknown",
        include_locked=True,
    )


def node_is_unknown_side_effect_barrier(analyzer: Any, node: A.ASTNode) -> bool:
    name = type(node).__name__
    if name == "Call":
        call_name = str(getattr(node, "name", "")).lower()
        known_functions: set[str] = getattr(
            analyzer, "_known_function_names_lower", set()
        )
        if call_name in known_functions:
            return False
        return call_name not in analyzer._PURE_CALL_NAMES
    return name in {
        "MethodCall",
        "BlockCall",
        "Spawn",
        "Join",
        "Await",
        "AtomicOp",
        "ChannelSend",
        "ChannelRecv",
        "ChannelTrySend",
        "ChannelTryRecv",
        "ChannelClose",
        "FieldAssign",
        "InlineAsm",
    }


def expr_contains_unknown_side_effect(analyzer: Any, expr: Optional[A.ASTNode]) -> bool:
    if expr is None:
        return False
    for node in analyzer._walk_ast(expr):
        if analyzer._node_is_unknown_side_effect_barrier(node):
            return True
    return False


def contains_unknown_side_effect_call(
    analyzer: Any, nodes: Iterable[A.ASTNode]
) -> bool:
    for node in nodes:
        for child in analyzer._walk_ast(node):
            if analyzer._node_is_unknown_side_effect_barrier(child):
                return True
    return False


def infer_array_info(
    analyzer: Any,
    expr: Optional[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
    interval_ctor: Callable[[int, int], Any],
) -> Optional[Tuple[Any, int]]:
    if expr is None:
        return None
    if isinstance(expr, A.ArrayLit):
        vals: List[int] = []
        for elem in expr.elements:
            if not isinstance(elem, A.Number) or not isinstance(elem.value, int):
                return None
            vals.append(int(elem.value))
        if not vals:
            return None
        return interval_ctor(min(vals), max(vals)), len(vals)
    if isinstance(expr, A.Variable):
        return facts.get_array_info(expr.name, func_scope)
    if isinstance(expr, A.Cast):
        return analyzer._infer_array_info(expr.expr, func_scope, facts, scope)
    if isinstance(expr, A.Call):
        return None
    if isinstance(expr, A.ArrayAccess):
        idx = facts._expr_interval(expr.index, func_scope, scope)
        if idx is None or idx.low != idx.high:
            return None
        if not isinstance(expr.array, A.Variable):
            return None
        base = facts.get_array_info(expr.array.name, func_scope)
        if base is None:
            return None
        elem_rng, arr_len = base
        index = idx.low
        if index < 0 or index >= arr_len:
            return None
        return elem_rng, 1
    return None


def observe_calls_in_expr(
    analyzer: Any,
    expr: Optional[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
) -> None:
    if expr is None:
        return
    for node in analyzer._walk_ast(expr):
        if not isinstance(node, A.Call):
            continue
        # Call hints describe entry calls into a function. Direct recursive
        # calls are handled by the callee's own range/refinement rules; unioning
        # them here pollutes bounded entry facts such as f(32) with the wider
        # recursive parameter range.
        if func_scope is not None and node.name == func_scope:
            continue
        for idx, arg in enumerate(node.args or []):
            interval = facts._expr_interval(arg, func_scope, scope)
            if interval is not None:
                facts.observe_call_arg_interval(node.name, idx, interval)
            info = _string_info_from_expr(facts, func_scope, arg, scope)
            if info is not None:
                facts.observe_call_arg_string_info(node.name, idx, info)


def _string_info_from_expr(
    facts: Any,
    func_scope: Optional[str],
    expr: A.ASTNode,
    scope: Optional[Dict[str, Any]] = None,
):
    if isinstance(expr, A.StringLit):
        from .range_facts_types import string_info_from_literal

        return string_info_from_literal(expr.value)
    if isinstance(expr, A.Variable):
        return facts.get_string_info(func_scope, expr.name)
    if isinstance(expr, A.Cast):
        return _string_info_from_expr(facts, func_scope, expr.expr, scope)
    if isinstance(expr, A.Call) and len(expr.args or []) == 1:
        from .range_facts_types import string_info_from_format_call

        arg = arg_at(expr, 0)
        rng = facts._expr_interval(arg, func_scope, dict(scope or {}))
        if rng is None:
            return None
        return string_info_from_format_call(expr.name, rng.low, rng.high)
    return None


def _strlen_source_var(expr: Optional[A.ASTNode]) -> Optional[str]:
    if not isinstance(expr, A.Call):
        return None
    if expr.name not in {"strlen", "len"} or len(expr.args or []) != 1:
        return None
    arg = arg_at(expr, 0)
    if isinstance(arg, A.Variable):
        return arg.name
    return None


def _remember_or_clear_strlen_var(
    facts: Any,
    func_scope: Optional[str],
    target_var: str,
    expr: Optional[A.ASTNode],
) -> None:
    facts.clear_string_len_var(func_scope, target_var)
    source = _strlen_source_var(expr)
    if source is not None:
        facts.set_string_len_var(func_scope, target_var, source)


def _remember_or_clear_string_info(
    facts: Any,
    func_scope: Optional[str],
    target_var: str,
    expr: Optional[A.ASTNode],
    scope: Optional[Dict[str, Any]] = None,
) -> None:
    facts.clear_string_info(func_scope, target_var)
    if expr is None:
        return
    info = _string_info_from_expr(facts, func_scope, expr, scope)
    if info is not None:
        facts.set_string_info(func_scope, target_var, info)


def _expr_roots_for_guarded_char_at(stmt: A.ASTNode) -> List[A.ASTNode]:
    """Expressions in a statement, excluding nested statement bodies.

    This keeps symbolic loop-bound proofs local to expressions reached before a
    top-level mutation in the current loop body. Nested loops get their own
    guard proof when they are scanned.
    """
    if isinstance(stmt, A.VarDecl):
        return [stmt.init_value] if stmt.init_value is not None else []
    if isinstance(stmt, A.Assign):
        return [stmt.value]
    if isinstance(stmt, A.RangeVarDecl):
        roots: List[A.ASTNode] = []
        if stmt.init_value is not None:
            roots.append(stmt.init_value)
        roots.extend([stmt.range_type.low, stmt.range_type.high])
        return roots
    if isinstance(stmt, A.Return):
        return [stmt.value] if stmt.value is not None else []
    if isinstance(stmt, A.Assert):
        roots = [stmt.condition]
        if stmt.message is not None:
            roots.append(stmt.message)
        return roots
    if isinstance(stmt, A.If):
        return [stmt.cond]
    if isinstance(stmt, (A.While, A.DoWhile, A.For)):
        roots = []
        for attr in ("cond", "max_iterations"):
            value = getattr(stmt, attr, None)
            if isinstance(value, A.ASTNode):
                roots.append(value)
        return roots
    if isinstance(stmt, A.FieldAssign):
        return [stmt.object_expr, stmt.value]
    if isinstance(stmt, A.DictAssign):
        return [stmt.dict_expr, stmt.key_expr, stmt.value_expr]
    return []


def _stmt_assigns_any(stmt: A.ASTNode, names: Set[str]) -> bool:
    if isinstance(stmt, (A.Assign, A.VarDecl, A.RangeVarDecl)):
        return getattr(stmt, "var_name", None) in names
    if isinstance(stmt, A.TupleAssign):
        return any(name in names for name in stmt.var_names)
    return False


def _intersect_interval(
    current: Any, low: int, high: int, interval_ctor: Callable[[int, int], Any]
):
    if current is not None:
        low = max(low, current.low)
        high = min(high, current.high)
    if low > high:
        return None
    return interval_ctor(low, high)


def _refine_scope_with_var_bound(
    scope: Dict[str, Any],
    var_name: str,
    *,
    low: int,
    high: int,
    interval_ctor: Callable[[int, int], Any],
) -> None:
    refined = _intersect_interval(scope.get(var_name), low, high, interval_ctor)
    if refined is None:
        # Empty intersections mean the guarded branch is unreachable under the
        # already-proven facts. Preserve the existing range so code emitted for
        # that syntactic branch can still benefit from the dominating proof.
        if scope.get(var_name) is None:
            scope.pop(var_name, None)
    else:
        scope[var_name] = refined


def _refine_scope_for_condition(
    cond: A.ASTNode,
    scope: Dict[str, Any],
    *,
    truthy: bool,
    interval_ctor: Callable[[int, int], Any],
) -> None:
    if not isinstance(cond, A.BinaryOp):
        return
    op = cond.op
    left = cond.left
    right = cond.right
    if (
        isinstance(left, A.Number)
        and isinstance(left.value, int)
        and isinstance(right, A.Variable)
    ):
        inverse = {
            "<": ">",
            "<=": ">=",
            ">": "<",
            ">=": "<=",
        }.get(op)
        if inverse is None:
            return
        left, right, op = right, left, inverse
    if not isinstance(left, A.Variable):
        return
    if not isinstance(right, A.Number) or not isinstance(right.value, int):
        return
    var_name = left.name
    value = int(right.value)
    min_i64 = -(1 << 63)
    max_i64 = (1 << 63) - 1
    if truthy:
        bounds = {
            ">=": (value, max_i64),
            ">": (value + 1, max_i64),
            "<=": (min_i64, value),
            "<": (min_i64, value - 1),
            "==": (value, value),
        }.get(op)
    else:
        bounds = {
            ">=": (min_i64, value - 1),
            ">": (min_i64, value),
            "<=": (value + 1, max_i64),
            "<": (value, max_i64),
        }.get(op)
        if bounds is None and op == "==":
            current = scope.get(var_name)
            if current is not None and current.low == value and current.high > value:
                bounds = (value + 1, current.high)
            elif current is not None and current.high == value and current.low < value:
                bounds = (current.low, value - 1)
    if bounds is None:
        return
    # Literal comparisons are safe narrowing points for both unknown integer-like
    # values and already-tracked intervals. The intersection keeps any stronger
    # range facts already proven by declarations, loop guards, or call hints.
    _refine_scope_with_var_bound(
        scope,
        var_name,
        low=bounds[0],
        high=bounds[1],
        interval_ctor=interval_ctor,
    )


def _relation_for_condition(
    cond: A.ASTNode, *, truthy: bool
) -> Optional[Tuple[str, str, str]]:
    if not isinstance(cond, A.BinaryOp):
        return None
    if cond.op not in {">", ">=", "<", "<=", "=="}:
        return None
    if not isinstance(cond.left, A.Variable) or not isinstance(cond.right, A.Variable):
        return None
    op = cond.op
    if not truthy:
        inverse = {
            ">": "<=",
            ">=": "<",
            "<": ">=",
            "<=": ">",
            "==": "!=",
        }.get(op)
        if inverse is None or inverse == "!=":
            return None
        op = inverse
    return cond.left.name, op, cond.right.name


def _drop_relations_for_var(
    facts: Any, func_scope: Optional[str], var_name: str
) -> None:
    relations = facts.scope_relations.get(func_scope)
    if not relations:
        return
    facts.scope_relations[func_scope] = {
        rel for rel in relations if rel[0] != var_name and rel[2] != var_name
    }


def _relations_with_condition(
    base: Set[Tuple[str, str, str]], cond: A.ASTNode, *, truthy: bool
) -> Set[Tuple[str, str, str]]:
    out = set(base)
    relation = _relation_for_condition(cond, truthy=truthy)
    if relation is not None:
        out.add(relation)
    return out


def _body_exits_current_path(body: List[A.ASTNode]) -> bool:
    if not body:
        return False
    return isinstance(body[-1], (A.Break, A.Continue, A.Return, A.Throw))


def _all_assignments_are_positive_increment(
    analyzer: Any, nodes: Iterable[A.ASTNode], var_name: str
) -> bool:
    found = False
    for stmt in nodes:
        for node in analyzer._walk_ast(stmt):
            if not isinstance(node, A.Assign) or node.var_name != var_name:
                continue
            found = True
            value = node.value
            if not isinstance(value, A.BinaryOp) or value.op != "+":
                return False
            if not isinstance(value.left, A.Variable) or value.left.name != var_name:
                return False
            if not isinstance(value.right, A.Number) or not isinstance(
                value.right.value, int
            ):
                return False
            if int(value.right.value) <= 0:
                return False
    return found
