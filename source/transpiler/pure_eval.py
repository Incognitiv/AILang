"""Conservative compile-time evaluator for pure integer/string call shapes."""

from __future__ import annotations

from dataclasses import dataclass
from parser import ast as A
from typing import Any


class PureEvalUnsupported(Exception):
    """Raised when an expression or statement cannot be safely evaluated."""


@dataclass
class _ReturnSignal(Exception):
    value: Any


class _BreakSignal(Exception):
    pass


def stable_literal_bindings(body: list[A.ASTNode]) -> dict[str, Any]:
    """Return names assigned exactly once to a literal in a function body."""
    assigned: dict[str, Any] = {}
    invalid: set[str] = set()

    def note(name: str, value: Any, ok: bool) -> None:
        if name in assigned or name in invalid or not ok:
            assigned.pop(name, None)
            invalid.add(name)
            return
        assigned[name] = value

    def walk(node: Any) -> None:
        if isinstance(node, A.VarDecl):
            value = _literal_value(node.init_value)
            note(node.var_name, value, value is not None)
            return
        if isinstance(node, A.Assign):
            value = _literal_value(node.value)
            note(node.var_name, value, value is not None)
            return
        if isinstance(node, A.ASTNode):
            for child in vars(node).values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    for stmt in body:
        walk(stmt)
    return assigned


def try_eval_call(
    function_nodes: dict[str, A.Function],
    node: A.Call,
    outer_bindings: dict[str, Any] | None = None,
) -> Any | None:
    try:
        args = [
            _eval_expr(arg, dict(outer_bindings or {}), function_nodes)
            for arg in node.args
        ]
        return _eval_function(function_nodes, node.name, args, depth=0)
    except PureEvalUnsupported:
        return None


def _literal_value(node: A.ASTNode | None) -> Any | None:
    if isinstance(node, A.StringLit):
        return node.value
    if isinstance(node, A.Number) and not getattr(node, "is_float", False):
        return int(node.value)
    if isinstance(node, A.Bool):
        return bool(node.value)
    return None


def _eval_function(
    function_nodes: dict[str, A.Function],
    name: str,
    args: list[Any],
    *,
    depth: int,
) -> Any:
    if depth > 8:
        raise PureEvalUnsupported
    func = function_nodes.get(name)
    if func is None:
        raise PureEvalUnsupported
    if len(args) != len(func.params):
        raise PureEvalUnsupported
    env = {param[0]: value for param, value in zip(func.params, args)}
    try:
        _exec_block(func.body, env, function_nodes, depth=depth)
    except _ReturnSignal as ret:
        return ret.value
    raise PureEvalUnsupported


def _exec_block(
    body: list[A.ASTNode],
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int,
) -> None:
    for stmt in body:
        signal = _exec_stmt(stmt, env, function_nodes, depth=depth)
        if isinstance(signal, (_ReturnSignal, _BreakSignal)):
            raise signal


def _exec_stmt(
    stmt: A.ASTNode,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int,
) -> _ReturnSignal | _BreakSignal | None:
    if isinstance(stmt, A.VarDecl):
        env[stmt.var_name] = _eval_expr(
            stmt.init_value, env, function_nodes, depth=depth
        )
        return None
    if isinstance(stmt, A.Assign):
        env[stmt.var_name] = _eval_expr(stmt.value, env, function_nodes, depth=depth)
        return None
    if isinstance(stmt, A.Return):
        return _ReturnSignal(_eval_expr(stmt.value, env, function_nodes, depth=depth))
    if isinstance(stmt, A.Break):
        return _BreakSignal()
    if isinstance(stmt, A.If):
        if _truthy(_eval_expr(stmt.cond, env, function_nodes, depth=depth)):
            return _exec_signal(stmt.then_body, env, function_nodes, depth)
        for cond, branch in getattr(stmt, "elsif_branches", []) or []:
            if _truthy(_eval_expr(cond, env, function_nodes, depth=depth)):
                return _exec_signal(branch, env, function_nodes, depth)
        return _exec_signal(stmt.else_body or [], env, function_nodes, depth)
    if isinstance(stmt, A.While):
        guard = 0
        while _truthy(_eval_expr(stmt.cond, env, function_nodes, depth=depth)):
            guard += 1
            if guard > 10000:
                raise PureEvalUnsupported
            signal = _exec_signal(stmt.body, env, function_nodes, depth)
            if isinstance(signal, _ReturnSignal):
                return signal
            if isinstance(signal, _BreakSignal):
                break
        return None
    raise PureEvalUnsupported


def _exec_signal(
    body: list[A.ASTNode],
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    depth: int,
) -> _ReturnSignal | _BreakSignal | None:
    try:
        _exec_block(body, env, function_nodes, depth=depth)
    except _ReturnSignal as ret:
        return ret
    except _BreakSignal as brk:
        return brk
    return None


def _eval_expr(
    expr: A.ASTNode | None,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int = 0,
) -> Any:
    if isinstance(expr, A.Number):
        if getattr(expr, "is_float", False):
            raise PureEvalUnsupported
        return int(expr.value)
    if isinstance(expr, A.Bool):
        return bool(expr.value)
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.Variable):
        if expr.name not in env:
            raise PureEvalUnsupported
        return env[expr.name]
    if isinstance(expr, A.UnaryOp):
        value = _eval_expr(expr.operand, env, function_nodes, depth=depth)
        if expr.op == "-":
            return -_to_int(value)
        if expr.op in {"!", "not"}:
            return 0 if _truthy(value) else 1
        raise PureEvalUnsupported
    if isinstance(expr, A.BinaryOp):
        left = _eval_expr(expr.left, env, function_nodes, depth=depth)
        right = _eval_expr(expr.right, env, function_nodes, depth=depth)
        return _eval_binary(expr.op, left, right)
    if isinstance(expr, A.Call):
        return _eval_call(expr, env, function_nodes, depth=depth)
    raise PureEvalUnsupported


def _eval_binary(op: str, left: Any, right: Any) -> Any:
    if op == "+":
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return _to_int(left) + _to_int(right)
    if op == "-":
        return _to_int(left) - _to_int(right)
    if op == "*":
        return _to_int(left) * _to_int(right)
    if op == "/":
        divisor = _to_int(right)
        if divisor == 0:
            raise PureEvalUnsupported
        return _to_int(left) // divisor
    if op == "%":
        divisor = _to_int(right)
        if divisor == 0:
            raise PureEvalUnsupported
        return _to_int(left) % divisor
    if op == "<":
        return _to_int(left) < _to_int(right)
    if op == "<=":
        return _to_int(left) <= _to_int(right)
    if op == ">":
        return _to_int(left) > _to_int(right)
    if op == ">=":
        return _to_int(left) >= _to_int(right)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op.lower() == "and":
        return _truthy(left) and _truthy(right)
    if op.lower() == "or":
        return _truthy(left) or _truthy(right)
    raise PureEvalUnsupported


def _eval_call(
    node: A.Call,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int,
) -> Any:
    args = [_eval_expr(arg, env, function_nodes, depth=depth) for arg in node.args]
    if len(args) == 1:
        (single_arg,) = args
        if node.name == "strlen" and isinstance(single_arg, str):
            return len(single_arg)
        if node.name == "len" and isinstance(single_arg, str):
            return len(single_arg)
    if node.name == "char_at" and len(args) == 2:
        text, index = args
        if not isinstance(text, str):
            raise PureEvalUnsupported
        i = _to_int(index)
        if i < 0 or i >= len(text):
            raise PureEvalUnsupported
        return ord(text[i])
    return _eval_function(function_nodes, node.name, args, depth=depth + 1)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _to_int(value) != 0


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    raise PureEvalUnsupported
