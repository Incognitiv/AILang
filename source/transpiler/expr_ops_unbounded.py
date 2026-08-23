"""LLVM lowering overrides for arbitrary-precision integer expressions.

Exponentiation keeps the exponent as a BigInt all the way into the pure-AIL
implementation.  This service also closes a type-discovery asymmetry: C already
recognizes unbounded record/class fields, while the base LLVM emitter historically
recognized only variables, calls, binary expressions and unary expressions.
Shift counts keep the existing checked-i64 contract.
"""

from __future__ import annotations

from llvmlite import ir
from parser.ast import BinaryOp, FieldAccess, ThisExpr, Variable, parsed_type_to_str

from transpiler.expr_ops import ExprOpsEmitter
from transpiler.llvm_bigint import is_unbounded_spec


class UnboundedPowExprOpsEmitter(ExprOpsEmitter):
    def _record_name_for_expr(self, node) -> str | None:
        if isinstance(node, ThisExpr):
            current = getattr(self.codegen, "current_class", None)
            if isinstance(current, str) and current in self.codegen.record_fields:
                return current
            return None

        if isinstance(node, Variable):
            class_name = self.codegen.get_variable_class_type(node.name)
            if class_name is not None:
                return class_name
            spec = getattr(self.codegen, "local_decl_types", {}).get(node.name)
            if spec is None:
                spec = getattr(self.codegen, "global_decl_types", {}).get(node.name)
            if spec is not None:
                name = parsed_type_to_str(spec)
                if name in self.codegen.record_fields:
                    return name
            return None

        if isinstance(node, FieldAccess):
            parent = self._record_name_for_expr(node.object_expr)
            if parent is None:
                return None
            try:
                _index, field_type = self.codegen.get_field_info(parent, node.field_name)
            except Exception:
                return None
            name = parsed_type_to_str(field_type)
            if name in self.codegen.record_fields:
                return name
        return None

    def _expr_is_unbounded(self, node) -> bool:
        if isinstance(node, FieldAccess):
            record_name = self._record_name_for_expr(node.object_expr)
            if record_name is not None:
                try:
                    _index, field_type = self.codegen.get_field_info(
                        record_name, node.field_name
                    )
                except Exception:
                    return False
                return is_unbounded_spec(self.codegen, field_type)
        return super()._expr_is_unbounded(node)

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
