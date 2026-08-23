from __future__ import annotations

from parser import ast as A
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ast_access import arg_at

from .range_facts_utils import (
    _all_assignments_are_positive_increment,
    _expr_roots_for_guarded_char_at,
    _relations_with_condition,
    _stmt_assigns_any,
)


def _decimal_accumulator_target(
    body: List[A.ASTNode],
    *,
    string_name: str,
    index_name: str,
) -> Optional[str]:
    digit_var: Optional[str] = None
    saw_low_guard = False
    saw_high_guard = False
    for stmt in body:
        if isinstance(stmt, A.Assign) and isinstance(stmt.value, A.Call):
            call = stmt.value
            if (
                call.name == "char_at"
                and len(call.args or []) >= 2
                and isinstance(arg_at(call, 0), A.Variable)
                and arg_at(call, 0).name == string_name
                and isinstance(arg_at(call, 1), A.Variable)
                and arg_at(call, 1).name == index_name
            ):
                digit_var = stmt.var_name
                saw_low_guard = False
                saw_high_guard = False
                continue
        if digit_var is None:
            continue
        if _is_break_guard_for(stmt, digit_var, "<", 48):
            saw_low_guard = True
            continue
        if _is_break_guard_for(stmt, digit_var, ">", 57):
            saw_high_guard = True
            continue
        if not isinstance(stmt, A.Assign):
            continue
        expr = stmt.value
        if not (
            isinstance(expr, A.BinaryOp)
            and expr.op == "+"
            and isinstance(expr.left, A.BinaryOp)
            and expr.left.op == "*"
            and isinstance(expr.left.left, A.Variable)
            and expr.left.left.name == stmt.var_name
            and isinstance(expr.left.right, A.Number)
            and int(expr.left.right.value) == 10
            and isinstance(expr.right, A.BinaryOp)
            and expr.right.op == "-"
            and isinstance(expr.right.left, A.Variable)
            and expr.right.left.name == digit_var
            and isinstance(expr.right.right, A.Number)
            and int(expr.right.right.value) == 48
        ):
            continue
        if saw_low_guard and saw_high_guard:
            return stmt.var_name
    return None


def _is_break_guard_for(stmt: A.ASTNode, var_name: str, op: str, value: int) -> bool:
    if not isinstance(stmt, A.If) or stmt.else_body:
        return False
    if len(stmt.then_body) != 1 or not isinstance(stmt.then_body[0], A.Break):
        return False
    cond = stmt.cond
    return (
        isinstance(cond, A.BinaryOp)
        and cond.op == op
        and isinstance(cond.left, A.Variable)
        and cond.left.name == var_name
        and isinstance(cond.right, A.Number)
        and int(cond.right.value) == value
    )


def _positive_modulus(expr: A.ASTNode) -> Optional[int]:
    if not isinstance(expr, A.BinaryOp) or expr.op not in {"%", "mod"}:
        return None
    right = expr.right
    if not isinstance(right, A.Number) or not isinstance(right.value, int):
        return None
    value = int(right.value)
    return value if value > 0 else None


def _collect_assignments_by_var(
    analyzer: Any, nodes: Iterable[A.ASTNode]
) -> Dict[str, List[Tuple[A.Assign, Set[Tuple[str, str, str]]]]]:
    out: Dict[str, List[Tuple[A.Assign, Set[Tuple[str, str, str]]]]] = {}

    def collect(
        items: Iterable[A.ASTNode], relations: Set[Tuple[str, str, str]]
    ) -> None:
        for stmt in items:
            if isinstance(stmt, A.Assign):
                out.setdefault(stmt.var_name, []).append((stmt, set(relations)))
                continue
            if isinstance(stmt, A.If):
                collect(
                    stmt.then_body,
                    _relations_with_condition(relations, stmt.cond, truthy=True),
                )
                collect(
                    stmt.else_body or [],
                    _relations_with_condition(relations, stmt.cond, truthy=False),
                )
                continue
            for node in analyzer._walk_ast(stmt):
                if isinstance(node, A.Assign):
                    out.setdefault(node.var_name, []).append((node, set(relations)))

    collect(nodes, set())
    return out


def _expr_is_nonnegative(
    expr: A.ASTNode,
    *,
    facts: Any,
    func_scope: Optional[str],
    scope: Dict[str, Any],
    protocol_nonnegative: Set[str],
    relation_scope: Optional[Set[Tuple[str, str, str]]] = None,
) -> bool:
    interval, _reason = facts._expr_interval_with_reason(
        expr, func_scope, scope, relation_scope=relation_scope
    )
    if interval is not None:
        return interval.low >= 0
    if isinstance(expr, A.Number) and isinstance(expr.value, int):
        return int(expr.value) >= 0
    if isinstance(expr, A.Variable):
        return expr.name in protocol_nonnegative
    if isinstance(expr, A.Cast):
        return _expr_is_nonnegative(
            expr.expr,
            facts=facts,
            func_scope=func_scope,
            scope=scope,
            protocol_nonnegative=protocol_nonnegative,
            relation_scope=relation_scope,
        )
    if isinstance(expr, A.Call):
        ret = facts.function_return_ranges.get(expr.name)
        return ret is not None and ret.low >= 0
    if isinstance(expr, A.BinaryOp):
        if expr.op in {"+", "*"}:
            return _expr_is_nonnegative(
                expr.left,
                facts=facts,
                func_scope=func_scope,
                scope=scope,
                protocol_nonnegative=protocol_nonnegative,
                relation_scope=relation_scope,
            ) and _expr_is_nonnegative(
                expr.right,
                facts=facts,
                func_scope=func_scope,
                scope=scope,
                protocol_nonnegative=protocol_nonnegative,
                relation_scope=relation_scope,
            )
        if expr.op in {"%", "mod"}:
            return _positive_modulus(expr) is not None and _expr_is_nonnegative(
                expr.left,
                facts=facts,
                func_scope=func_scope,
                scope=scope,
                protocol_nonnegative=protocol_nonnegative,
                relation_scope=relation_scope,
            )
    return False


def _decimal_accumulator_targets(
    analyzer: Any,
    nodes: Iterable[A.ASTNode],
    *,
    string_name: str,
    index_name: str,
) -> Set[str]:
    targets: Set[str] = set()
    direct = _decimal_accumulator_target(
        list(nodes), string_name=string_name, index_name=index_name
    )
    if direct is not None:
        targets.add(direct)
    for stmt in nodes:
        for node in analyzer._walk_ast(stmt):
            if not isinstance(node, A.While):
                continue
            nested = _decimal_accumulator_target(
                node.body, string_name=string_name, index_name=index_name
            )
            if nested is not None:
                targets.add(nested)
    return targets


def _derive_modulo_assignment_ranges(
    analyzer: Any,
    nodes: Iterable[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
    *,
    protocol_nonnegative: Optional[Set[str]] = None,
) -> Tuple[Dict[str, Tuple[int, int]], Set[str]]:
    refinements: Dict[str, Tuple[int, int]] = {}
    preserve: Set[str] = set()
    nonnegative = protocol_nonnegative or set()
    assignments = _collect_assignments_by_var(analyzer, nodes)
    for var_name, writes in assignments.items():
        base = scope.get(var_name)
        if base is None or base.low < 0:
            continue
        max_high = base.high
        valid = True
        for write, relations in writes:
            value = write.value
            if not isinstance(value, A.BinaryOp):
                valid = False
                break
            modulus = _positive_modulus(value)
            if modulus is None:
                valid = False
                break
            if not _expr_is_nonnegative(
                value.left,
                facts=facts,
                func_scope=func_scope,
                scope=scope,
                protocol_nonnegative=nonnegative,
                relation_scope=relations,
            ):
                valid = False
                break
            max_high = max(max_high, modulus - 1)
        if not valid:
            continue
        # If the assignment is skipped on a branch, the pre-loop value can
        # survive. Keep only cases where the preserved range covers that value.
        if base.high > max_high:
            continue
        refinements[var_name] = (0, max_high)
        preserve.add(var_name)
    return refinements, preserve


def _derive_protocol_loop_ranges(
    analyzer: Any,
    node: A.While,
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
) -> Tuple[Dict[str, Tuple[int, int]], Set[str]]:
    refinements: Dict[str, Tuple[int, int]] = {}
    preserve: Set[str] = set()
    if not isinstance(node.cond, A.BinaryOp) or node.cond.op != "<":
        return refinements, preserve
    if not isinstance(node.cond.left, A.Variable) or not isinstance(
        node.cond.right, A.Variable
    ):
        return refinements, preserve
    index_name = node.cond.left.name
    len_name = node.cond.right.name
    string_name = facts.string_len_vars.get(func_scope, {}).get(len_name)
    if string_name is None:
        return refinements, preserve
    info = facts.get_string_info(func_scope, string_name)
    if info is None:
        return refinements, preserve
    protocol_nonnegative: Set[str] = set()

    current = scope.get(index_name)
    if (
        current is not None
        and current.low >= 0
        and info.max_len > 0
        and _all_assignments_are_positive_increment(analyzer, node.body, index_name)
    ):
        refinements[index_name] = (current.low, info.max_len - 1)
        preserve.add(index_name)
        protocol_nonnegative.add(index_name)

    decimal_targets = _decimal_accumulator_targets(
        analyzer, node.body, string_name=string_name, index_name=index_name
    )
    protocol_nonnegative.update(decimal_targets)

    if 0 < info.max_digit_run <= 18:
        max_value = (10**info.max_digit_run) - 1
        for target in decimal_targets:
            current_target = scope.get(target)
            if current_target is not None and current_target.low >= 0:
                refinements[target] = (0, max_value)
                preserve.add(target)

    modulo_refinements, modulo_preserve = _derive_modulo_assignment_ranges(
        analyzer,
        node.body,
        func_scope,
        facts,
        scope,
        protocol_nonnegative=protocol_nonnegative,
    )
    refinements.update(modulo_refinements)
    preserve.update(modulo_preserve)
    return refinements, preserve


def _mark_symbolic_guarded_char_at_calls(
    analyzer: Any,
    body: List[A.ASTNode],
    func_scope: Optional[str],
    facts: Any,
    scope: Dict[str, Any],
    cond: A.ASTNode,
) -> None:
    if not isinstance(cond, A.BinaryOp) or cond.op != "<":
        return
    if not isinstance(cond.left, A.Variable) or not isinstance(cond.right, A.Variable):
        return
    index_name = cond.left.name
    len_name = cond.right.name
    string_name = facts.string_len_vars.get(func_scope, {}).get(len_name)
    if string_name is None:
        return
    index_range = scope.get(index_name)
    has_nonnegative_proof = (
        index_range is not None and index_range.low >= 0
    ) or facts.is_nonnegative_var(func_scope, index_name)
    if not has_nonnegative_proof:
        return

    guard_names = {index_name, len_name, string_name}
    for stmt in body:
        for root in _expr_roots_for_guarded_char_at(stmt):
            for node in analyzer._walk_ast(root):
                if not isinstance(node, A.Call):
                    continue
                if node.name != "char_at" or len(node.args or []) < 2:
                    continue
                string_arg, index_arg = arg_at(node, 0), arg_at(node, 1)
                if (
                    isinstance(string_arg, A.Variable)
                    and string_arg.name == string_name
                    and isinstance(index_arg, A.Variable)
                    and index_arg.name == index_name
                ):
                    facts.mark_safe_char_at_call(func_scope, node)
        if _stmt_assigns_any(stmt, guard_names):
            break
