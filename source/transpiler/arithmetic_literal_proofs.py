"""Small literal-only arithmetic safety proofs shared by emitters."""

from __future__ import annotations

from parser import ast as A

from ast_access import arg_at
from transpiler.expr_string_fastpath import (
    literal_char_at_byte_value,
    literal_ord_byte_value,
    static_string_byte_length,
)


def int_literal_value(node: A.ASTNode) -> int | None:
    """Return an integer literal value, or ``None`` for non-literal nodes."""
    if isinstance(node, A.Call):
        if node.name in ("strlen", "len") and len(node.args) == 1:
            return static_string_byte_length(arg_at(node, 0))
        if node.name == "ord" and len(node.args) == 1:
            return literal_ord_byte_value(arg_at(node, 0))
        if node.name == "char_at" and len(node.args) >= 2:
            return literal_char_at_byte_value(arg_at(node, 0), arg_at(node, 1))
    if isinstance(node, A.UnaryOp):
        operand = int_literal_value(node.operand)
        if operand is None:
            return None
        op = str(node.op).lower()
        if op == "+":
            return operand
        if op == "-":
            return -operand
        return None
    if not isinstance(node, A.Number):
        return None
    if bool(getattr(node, "is_float", False)):
        return None
    try:
        return int(node.value)
    except (TypeError, ValueError, OverflowError):
        return None


def positive_int_literal(node: A.ASTNode) -> bool:
    """Return whether ``node`` is an integer literal greater than zero.

    This deliberately ignores unary forms and variables.  A positive integer
    literal is enough to prove signed division/modulo cannot hit zero divisor
    or INT_MIN / -1 style overflow.
    """
    value = int_literal_value(node)
    return value is not None and value > 0


def shift_amount_literal_in_range(node: A.ASTNode, bit_width: int) -> bool:
    """Return whether ``node`` is a literal shift amount in ``[0, bit_width)``."""
    value = int_literal_value(node)
    if value is None:
        return False
    return 0 <= value < bit_width


def int_literal_in_range(node: A.ASTNode, lower: int, upper_exclusive: int) -> bool:
    """Return whether ``node`` is an integer literal in ``[lower, upper)``."""
    value = int_literal_value(node)
    if value is None:
        return False
    return lower <= value < upper_exclusive


def int_literal_equals(node: A.ASTNode, expected: int) -> bool:
    """Return whether ``node`` is an integer literal equal to ``expected``."""
    value = int_literal_value(node)
    return value is not None and value == expected


def neutral_int_arithmetic_safe(node: A.BinaryOp) -> str | None:
    """Return a no-overflow reason for safe algebraic identity operations.

    The proof intentionally covers only identities where evaluating both
    operands and doing raw machine arithmetic cannot overflow. It does not
    claim `0 - x` is safe because signed `0 - INT_MIN` can overflow.
    """
    op = str(node.op).lower()
    if op in {"+", "plus"}:
        if int_literal_equals(node.left, 0) or int_literal_equals(node.right, 0):
            return "neutral_add_zero"
    if op in {"-", "minus"}:
        if int_literal_equals(node.right, 0):
            return "neutral_sub_zero"
    if op in {"*", "star"}:
        if int_literal_equals(node.left, 0) or int_literal_equals(node.right, 0):
            return "neutral_mul_zero"
        if int_literal_equals(node.left, 1) or int_literal_equals(node.right, 1):
            return "neutral_mul_one"
    return None


def literal_int_arithmetic_safe(
    node: A.BinaryOp, *, bit_width: int, is_unsigned: bool
) -> str | None:
    """Return a no-overflow reason for literal-only +, -, * operations."""
    if bit_width <= 0:
        return None
    left = int_literal_value(node.left)
    right = int_literal_value(node.right)
    if left is None or right is None:
        return None
    op = str(node.op).lower()
    if op in {"+", "plus"}:
        result = left + right
    elif op in {"-", "minus"}:
        result = left - right
    elif op in {"*", "star"}:
        result = left * right
    else:
        return None
    if is_unsigned:
        min_value = 0
        max_value = (1 << bit_width) - 1
    else:
        min_value = -(1 << (bit_width - 1))
        max_value = (1 << (bit_width - 1)) - 1
    if min_value <= result <= max_value:
        return "literal_arithmetic_in_range"
    return None
