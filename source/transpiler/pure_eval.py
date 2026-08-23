"""Conservative compile-time evaluator for pure integer/string call shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from parser import ast as A
from typing import Any


class PureEvalUnsupported(Exception):
    """Raised when an expression or statement cannot be safely evaluated."""


@dataclass
class _ReturnSignal(Exception):
    value: Any


@dataclass
class _EvalBudget:
    remaining: int
    memo: dict[tuple[str, tuple[Any, ...]], Any] = field(default_factory=dict)
    active: set[tuple[str, tuple[Any, ...]]] = field(default_factory=set)

    def spend(self, amount: int = 1) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise PureEvalUnsupported


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
        budget = _EvalBudget(250_000)
        args = [
            _eval_expr(arg, dict(outer_bindings or {}), function_nodes, budget=budget)
            for arg in node.args
        ]
        return _eval_function(function_nodes, node.name, args, depth=0, budget=budget)
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
    budget: _EvalBudget,
) -> Any:
    budget.spend()
    func = function_nodes.get(name)
    if func is None:
        raise PureEvalUnsupported
    if len(args) != len(func.params):
        raise PureEvalUnsupported
    key = _memo_key(name, args)
    if key is not None:
        if key in budget.memo:
            return budget.memo[key]
        if key in budget.active:
            raise PureEvalUnsupported
    if depth > 128:
        raise PureEvalUnsupported
    env = {param[0]: value for param, value in zip(func.params, args)}
    if key is not None:
        budget.active.add(key)
    try:
        _exec_block(func.body, env, function_nodes, depth=depth, budget=budget)
    except _ReturnSignal as ret:
        if key is not None:
            budget.memo[key] = ret.value
        return ret.value
    finally:
        if key is not None:
            budget.active.discard(key)
    raise PureEvalUnsupported


def _memo_key(name: str, args: list[Any]) -> tuple[str, tuple[Any, ...]] | None:
    try:
        key_args = tuple(_freeze_value(arg) for arg in args)
        hash((name, key_args))
    except (PureEvalUnsupported, TypeError):
        return None
    return name, key_args


def _freeze_value(value: Any) -> Any:
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    raise PureEvalUnsupported


def _exec_block(
    body: list[A.ASTNode],
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int,
    budget: _EvalBudget,
) -> None:
    for stmt in body:
        signal = _exec_stmt(stmt, env, function_nodes, depth=depth, budget=budget)
        if isinstance(signal, (_ReturnSignal, _BreakSignal)):
            raise signal


def _exec_stmt(
    stmt: A.ASTNode,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    *,
    depth: int,
    budget: _EvalBudget,
) -> _ReturnSignal | _BreakSignal | None:
    budget.spend()
    if isinstance(stmt, A.VarDecl):
        env[stmt.var_name] = _eval_expr(
            stmt.init_value, env, function_nodes, depth=depth, budget=budget
        )
        return None
    if isinstance(stmt, A.RangeVarDecl):
        if stmt.init_value is None:
            env[stmt.var_name] = _eval_expr(
                stmt.range_type.low, env, function_nodes, depth=depth, budget=budget
            )
        else:
            env[stmt.var_name] = _eval_expr(
                stmt.init_value, env, function_nodes, depth=depth, budget=budget
            )
        return None
    if isinstance(stmt, A.Assign):
        env[stmt.var_name] = _eval_expr(
            stmt.value, env, function_nodes, depth=depth, budget=budget
        )
        return None
    if isinstance(stmt, A.Return):
        return _ReturnSignal(
            _eval_expr(stmt.value, env, function_nodes, depth=depth, budget=budget)
        )
    if isinstance(stmt, A.Break):
        return _BreakSignal()
    if isinstance(stmt, A.If):
        if _truthy(
            _eval_expr(stmt.cond, env, function_nodes, depth=depth, budget=budget)
        ):
            return _exec_signal(stmt.then_body, env, function_nodes, depth, budget)
        for cond, branch in getattr(stmt, "elsif_branches", []) or []:
            if _truthy(
                _eval_expr(cond, env, function_nodes, depth=depth, budget=budget)
            ):
                return _exec_signal(branch, env, function_nodes, depth, budget)
        return _exec_signal(stmt.else_body or [], env, function_nodes, depth, budget)
    if isinstance(stmt, A.While):
        if _try_exec_fixed_array_reduction(stmt, env, function_nodes, depth, budget):
            return None
        while _truthy(
            _eval_expr(stmt.cond, env, function_nodes, depth=depth, budget=budget)
        ):
            budget.spend()
            signal = _exec_signal(stmt.body, env, function_nodes, depth, budget)
            if isinstance(signal, _ReturnSignal):
                return signal
            if isinstance(signal, _BreakSignal):
                break
        return None
    raise PureEvalUnsupported


def _try_exec_fixed_array_reduction(
    stmt: A.While,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    depth: int,
    budget: _EvalBudget,
) -> bool:
    """Fold loops shaped as repeated fixed-array reductions.

    Recognized shape:
      while i < limit then
        j := 0..N = 0
        while true then
          acc = acc + arr[j]
          if j == K then break end
          j = j + 1
        end
        i = i + 1
      end
    """
    budget.spend(32)
    loop = _outer_counted_loop(stmt, env, function_nodes, depth, budget)
    if loop is None:
        return False
    counter_name, counter_start, loop_limit = loop
    if loop_limit <= counter_start:
        env[counter_name] = counter_start
        return True
    if len(stmt.body) != 3:
        return False
    idx_decl, inner, counter_step = stmt.body
    if not isinstance(idx_decl, A.RangeVarDecl) or not isinstance(inner, A.While):
        return False
    if not _is_number_value(idx_decl.init_value, 0):
        return False
    idx_name = idx_decl.var_name
    reduction = _inner_fixed_array_reduction(inner, idx_name)
    if reduction is None:
        return False
    acc_name, array_name, last_index = reduction
    if not _is_increment(counter_step, counter_name, 1):
        return False
    arr_value = env.get(array_name)
    acc_value = env.get(acc_name)
    if not isinstance(arr_value, list) or not isinstance(acc_value, int):
        return False
    if last_index < 0 or last_index >= len(arr_value):
        return False
    per_iteration = 0
    for value in arr_value[: last_index + 1]:
        per_iteration += _to_int(value)
    iterations = loop_limit - counter_start
    env[acc_name] = acc_value + iterations * per_iteration
    env[counter_name] = loop_limit
    return True


def _outer_counted_loop(
    stmt: A.While,
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    depth: int,
    budget: _EvalBudget,
) -> tuple[str, int, int] | None:
    cond = stmt.cond
    if not isinstance(cond, A.BinaryOp) or cond.op != "<":
        return None
    if not isinstance(cond.left, A.Variable):
        return None
    counter_name = cond.left.name
    counter_start = env.get(counter_name)
    if not isinstance(counter_start, int):
        return None
    try:
        loop_limit = _to_int(
            _eval_expr(cond.right, env, function_nodes, depth=depth, budget=budget)
        )
    except PureEvalUnsupported:
        return None
    return counter_name, counter_start, loop_limit


def _inner_fixed_array_reduction(
    inner: A.While,
    idx_name: str,
) -> tuple[str, str, int] | None:
    return _inner_break_array_reduction(
        inner, idx_name
    ) or _inner_counted_array_reduction(inner, idx_name)


def _inner_break_array_reduction(
    inner: A.While,
    idx_name: str,
) -> tuple[str, str, int] | None:
    if not isinstance(inner.cond, A.Bool) or not inner.cond.value:
        return None
    if len(inner.body) != 3:
        return None
    add_stmt, break_if, idx_step = inner.body
    reduction = _array_reduction_add(add_stmt, idx_name)
    if reduction is None:
        return None
    last_index = _break_index(break_if, idx_name)
    if last_index is None:
        return None
    if not _is_increment(idx_step, idx_name, 1):
        return None
    acc_name, array_name = reduction
    return acc_name, array_name, last_index


def _inner_counted_array_reduction(
    inner: A.While,
    idx_name: str,
) -> tuple[str, str, int] | None:
    limit = _counted_index_limit(inner.cond, idx_name)
    if limit is None or limit <= 0:
        return None
    if len(inner.body) != 2:
        return None
    add_stmt, idx_step = inner.body
    reduction = _array_reduction_add(add_stmt, idx_name)
    if reduction is None:
        return None
    if not _is_increment(idx_step, idx_name, 1):
        return None
    acc_name, array_name = reduction
    return acc_name, array_name, limit - 1


def _counted_index_limit(cond: A.ASTNode, idx_name: str) -> int | None:
    if not isinstance(cond, A.BinaryOp) or cond.op != "<":
        return None
    if not isinstance(cond.left, A.Variable) or cond.left.name != idx_name:
        return None
    if not isinstance(cond.right, A.Number) or getattr(cond.right, "is_float", False):
        return None
    return int(cond.right.value)


def _array_reduction_add(
    stmt: A.ASTNode,
    idx_name: str,
) -> tuple[str, str] | None:
    if not isinstance(stmt, A.Assign):
        return None
    value = stmt.value
    if not isinstance(value, A.BinaryOp) or value.op != "+":
        return None
    if not isinstance(value.left, A.Variable) or value.left.name != stmt.var_name:
        return None
    access = value.right
    if not isinstance(access, A.ArrayAccess):
        return None
    if not isinstance(access.array, A.Variable):
        return None
    if not isinstance(access.index, A.Variable) or access.index.name != idx_name:
        return None
    return stmt.var_name, access.array.name


def _break_index(stmt: A.ASTNode, idx_name: str) -> int | None:
    if not isinstance(stmt, A.If):
        return None
    if len(stmt.then_body) != 1 or not isinstance(stmt.then_body[0], A.Break):
        return None
    if stmt.else_body:
        return None
    cond = stmt.cond
    if not isinstance(cond, A.BinaryOp) or cond.op != "==":
        return None
    if not isinstance(cond.left, A.Variable) or cond.left.name != idx_name:
        return None
    if not isinstance(cond.right, A.Number) or getattr(cond.right, "is_float", False):
        return None
    return int(cond.right.value)


def _is_increment(stmt: A.ASTNode, var_name: str, amount: int) -> bool:
    if not isinstance(stmt, A.Assign) or stmt.var_name != var_name:
        return False
    value = stmt.value
    if not isinstance(value, A.BinaryOp) or value.op != "+":
        return False
    if not isinstance(value.left, A.Variable) or value.left.name != var_name:
        return False
    return _is_number_value(value.right, amount)


def _is_number_value(node: A.ASTNode | None, expected: int) -> bool:
    return (
        isinstance(node, A.Number)
        and not getattr(node, "is_float", False)
        and int(node.value) == expected
    )


def _exec_signal(
    body: list[A.ASTNode],
    env: dict[str, Any],
    function_nodes: dict[str, A.Function],
    depth: int,
    budget: _EvalBudget,
) -> _ReturnSignal | _BreakSignal | None:
    try:
        _exec_block(body, env, function_nodes, depth=depth, budget=budget)
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
    budget: _EvalBudget,
) -> Any:
    budget.spend()
    if isinstance(expr, A.Number):
        if getattr(expr, "is_float", False):
            raise PureEvalUnsupported
        return int(expr.value)
    if isinstance(expr, A.Bool):
        return bool(expr.value)
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.ArrayLit):
        return [
            _eval_expr(item, env, function_nodes, depth=depth, budget=budget)
            for item in expr.elements
        ]
    if isinstance(expr, A.Variable):
        if expr.name not in env:
            raise PureEvalUnsupported
        return env[expr.name]
    if isinstance(expr, A.UnaryOp):
        value = _eval_expr(
            expr.operand, env, function_nodes, depth=depth, budget=budget
        )
        if expr.op == "-":
            return -_to_int(value)
        if expr.op in {"!", "not"}:
            return 0 if _truthy(value) else 1
        raise PureEvalUnsupported
    if isinstance(expr, A.BinaryOp):
        left = _eval_expr(expr.left, env, function_nodes, depth=depth, budget=budget)
        right = _eval_expr(expr.right, env, function_nodes, depth=depth, budget=budget)
        return _eval_binary(expr.op, left, right)
    if isinstance(expr, A.DictAccess):
        container = _eval_expr(
            expr.dict_expr, env, function_nodes, depth=depth, budget=budget
        )
        index = _to_int(
            _eval_expr(expr.key_expr, env, function_nodes, depth=depth, budget=budget)
        )
        if not isinstance(container, list) or index < 0 or index >= len(container):
            raise PureEvalUnsupported
        return container[index]
    if isinstance(expr, A.ArrayAccess):
        container = _eval_expr(
            expr.array, env, function_nodes, depth=depth, budget=budget
        )
        index = _to_int(
            _eval_expr(expr.index, env, function_nodes, depth=depth, budget=budget)
        )
        if not isinstance(container, list) or index < 0 or index >= len(container):
            raise PureEvalUnsupported
        return container[index]
    if isinstance(expr, A.Call):
        return _eval_call(expr, env, function_nodes, depth=depth, budget=budget)
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
    budget: _EvalBudget,
) -> Any:
    args = [
        _eval_expr(arg, env, function_nodes, depth=depth, budget=budget)
        for arg in node.args
    ]
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
    return _eval_function(
        function_nodes, node.name, args, depth=depth + 1, budget=budget
    )


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
