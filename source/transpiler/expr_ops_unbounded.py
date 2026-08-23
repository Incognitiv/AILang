"""LLVM lowering override for true ``unbounded ** unbounded``.

Only exponentiation differs from the base ExprOpsEmitter: the exponent remains
a BigInt all the way into the pure-AIL exponentiation module. Shift counts keep
the existing checked-i64 contract.
"""

from __future__ import annotations

from llvmlite import ir
from parser.ast import BinaryOp

from transpiler.expr_ops import ExprOpsEmitter


class UnboundedPowExprOpsEmitter(ExprOpsEmitter):
    def _visit_bigint_binary(self, node: BinaryOp) -> ir.Value:
        if node.op.lower() not in {"**", "^", "power"}:
            return super()._visit_bigint_binary(node)

        left, own_left = self._bigint_operand(node.left)
        exponent, own_exponent = self._bigint_operand(node.right)
        result = self.builder.call(
            self.codegen._get_bigint_pow(),
            [left, exponent],
            name="bigint_pow",
        )
        if own_left:
            self.builder.call(self.codegen._get_bigint_free(), [left])
        if own_exponent:
            self.builder.call(self.codegen._get_bigint_free(), [exponent])
        return result
