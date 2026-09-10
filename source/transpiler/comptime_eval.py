"""Backend-neutral evaluation of side-effect-free ``comptime`` expressions."""

from __future__ import annotations

from parser import ast as A
from typing import Any


class ComptimeEvaluationError(ValueError):
    """Raised when a compile-time expression is invalid rather than unknown."""


def evaluate_comptime(
    expr: A.ASTNode,
    *,
    target_os: str,
    target_backend: str,
    strict_arithmetic: bool = False,
) -> Any | None:
    """Evaluate an expression that is safe and fully known at compile time.

    ``None`` means that the expression is not statically known.  Callers that
    historically treated invalid arithmetic as a hard compile error can opt
    into ``strict_arithmetic`` without duplicating the evaluator.
    """
    if isinstance(expr, A.Number):
        return float(expr.value) if expr.is_float else int(expr.value)
    if isinstance(expr, A.Bool):
        return expr.value
    if isinstance(expr, A.StringLit):
        return expr.value

    if isinstance(expr, A.Call):
        if expr.args:
            return None
        if expr.name == "target_os":
            return target_os
        if expr.name == "target_backend":
            return target_backend
        return None

    if isinstance(expr, A.BinaryOp):
        left = evaluate_comptime(
            expr.left,
            target_os=target_os,
            target_backend=target_backend,
            strict_arithmetic=strict_arithmetic,
        )
        right = evaluate_comptime(
            expr.right,
            target_os=target_os,
            target_backend=target_backend,
            strict_arithmetic=strict_arithmetic,
        )
        if left is None or right is None:
            return None

        op = expr.op
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                if strict_arithmetic:
                    raise ComptimeEvaluationError(
                        "Division by zero in comptime expression"
                    )
                return None
            return left // right if isinstance(left, int) else left / right
        if op == "%":
            if right == 0:
                if strict_arithmetic:
                    raise ComptimeEvaluationError(
                        "Modulo by zero in comptime expression"
                    )
                return None
            return left % right
        if op == "**":
            return left**right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op in {"and", "&&"}:
            return left and right
        if op in {"or", "||"}:
            return left or right
        return None

    if isinstance(expr, A.UnaryOp):
        operand = evaluate_comptime(
            expr.operand,
            target_os=target_os,
            target_backend=target_backend,
            strict_arithmetic=strict_arithmetic,
        )
        if operand is None:
            return None
        if expr.op == "-":
            return -operand
        if expr.op in {"not", "!"}:
            return not operand
        return None

    if isinstance(expr, A.TernaryOp):
        condition = evaluate_comptime(
            expr.cond,
            target_os=target_os,
            target_backend=target_backend,
            strict_arithmetic=strict_arithmetic,
        )
        if condition is None:
            return None
        branch = expr.true_expr if condition else expr.false_expr
        return evaluate_comptime(
            branch,
            target_os=target_os,
            target_backend=target_backend,
            strict_arithmetic=strict_arithmetic,
        )

    return None
