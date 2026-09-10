"""Shared argument and integer-boundary helpers for C call lowering."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from transpiler.c_bigint import is_unbounded_spec, owned_bigint_expr
from transpiler.codegen_int_ranges import expr_int_range, range_fits_int64
from transpiler.fixed_int_types import info_for_c_fixed

_NARROW_INT_LIMITS = {
    "int32_t": (-(1 << 31), (1 << 31) - 1),
    "int16_t": (-(1 << 15), (1 << 15) - 1),
    "int8_t": (-(1 << 7), (1 << 7) - 1),
    "uint32_t": (0, (1 << 32) - 1),
    "uint16_t": (0, (1 << 16) - 1),
    "uint8_t": (0, (1 << 8) - 1),
}
_PLAIN_C_INT_MIN = -(1 << 31)
_PLAIN_C_INT_MAX = (1 << 31) - 1


def _int_range_fits_c_type(self, node: A.ASTNode, c_type: str) -> bool:
    limits = _NARROW_INT_LIMITS.get(c_type)
    if limits is None:
        return False
    rng = expr_int_range(self, node)
    return rng is not None and limits[0] <= rng[0] and rng[1] <= limits[1]


def _runtime_i64_arg(self, node: A.ASTNode, expr: str) -> str:
    """Narrow a language integer to an i64 runtime boundary without data loss."""
    fixed = info_for_c_fixed(self._infer_type(node))
    if fixed is not None and fixed.bits > 64:
        return f"ailang_narrow_i64_{fixed.canonical}({expr})"
    return expr


def _plain_c_int_literal(node: A.ASTNode) -> str | None:
    if not isinstance(node, A.Number) or not isinstance(node.value, int):
        return None
    value = int(node.value)
    if _PLAIN_C_INT_MIN <= value <= _PLAIN_C_INT_MAX:
        return str(value)
    return None


def _narrow_integer_arg_expr(self, node: A.ASTNode, c_type: str) -> str:
    if not _int_range_fits_c_type(self, node, c_type):
        return self.expr(node)
    literal = _plain_c_int_literal(node)
    if literal is not None:
        return literal
    if isinstance(node, A.BinaryOp) and node.op in {"+", "-", "*"}:
        if not range_fits_int64(expr_int_range(self, node)):
            return self.expr(node)
        left = _narrow_integer_arg_expr(self, node.left, c_type)
        right = _narrow_integer_arg_expr(self, node.right, c_type)
        return f"({left} {node.op} {right})"
    return self.expr(node)


def build_call_args(self: Any, node: A.Call) -> tuple[list[A.ASTNode], list[str]]:
    call_arg_nodes = list(node.args)
    expected_param_specs = (
        self.functions[node.name][0] if node.name in self.functions else []
    )
    call_args = []
    for idx, arg in enumerate(call_arg_nodes):
        if idx < len(expected_param_specs) and is_unbounded_spec(
            self, expected_param_specs[idx]
        ):
            call_args.append(owned_bigint_expr(self, arg))
        else:
            call_args.append(self.expr(arg))
    return call_arg_nodes, call_args
