"""
Type/casting/operator helpers for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression
refactor.
"""

from __future__ import annotations

import sys
from parser.ast import Variable
from typing import Any

from llvmlite import ir
from transpiler.expr_common import ExprGenError


class ExprTypeHelperEmitter:
    """Type and arithmetic helpers for LLVM expressions."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen
        self._pow_intrinsic: Any | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _is_float_type(self, llvm_type: ir.Type) -> bool:
        return isinstance(llvm_type, (ir.FloatType, ir.DoubleType))

    def _both_strings(self, left: ir.Value, right: ir.Value) -> bool:
        return self._is_string_pointer(left) and self._is_string_pointer(right)

    def _is_string_pointer(self, value: ir.Value) -> bool:
        value_type = value.type
        if isinstance(value_type, ir.PointerType):
            pointee = value_type.pointee
            return isinstance(pointee, ir.IntType) and getattr(pointee, "width", 0) == 8
        return False

    def _byte_to_string(self, byte_val: ir.Value) -> ir.Value:
        """Convert a single i8 byte to a 2-character string (char + null terminator)."""
        # Allocate 2 bytes
        two = ir.Constant(ir.IntType(64), 2)
        buf = self.codegen.string_alloc(two, "byte_str_buf")
        # Store the byte
        self.builder.store(byte_val, buf)
        # Store null terminator
        one_i32 = ir.Constant(ir.IntType(32), 1)
        end_ptr = self.builder.gep(buf, [one_i32], name="byte_str_end")
        self.builder.store(ir.Constant(ir.IntType(8), 0), end_ptr)
        return buf

    def cast_value(
        self, value: ir.Value, target_type: ir.Type, unsigned: bool = False
    ) -> ir.Value:
        if value.type == target_type:
            return value
        if isinstance(value.type, ir.IntType) and self._is_float_type(target_type):
            # Use uitofp for unsigned integers, sitofp for signed
            if unsigned:
                return self.builder.uitofp(value, target_type, name="utof")
            return self.builder.sitofp(value, target_type, name="itof")
        if self._is_float_type(value.type) and isinstance(target_type, ir.IntType):
            # Safe float-to-int conversion with overflow checking
            return self._safe_fptosi(value, target_type)
        if isinstance(value.type, ir.IntType) and isinstance(target_type, ir.IntType):
            if value.type.width < target_type.width:
                ext_op = self.builder.zext if unsigned else self.builder.sext
                out = ext_op(value, target_type, name="ext")
                self.codegen.set_signedness(out, not unsigned)
                return out
            # Integer narrowing (trunc) - check for compile-time overflow
            # Type Safety 10/10: Warn if constant value would be truncated
            if isinstance(value, ir.Constant) and hasattr(value, "constant"):
                const_val = value.constant
                target_max = (1 << target_type.width) - 1
                target_min = -(1 << (target_type.width - 1)) if not unsigned else 0
                if const_val > target_max or const_val < target_min:

                    print(
                        f"Warning: Integer constant {const_val} truncated to "
                        f"{target_type.width}-bit (max {target_max})",
                        file=sys.stderr,
                    )
            out = self.builder.trunc(value, target_type, name="trunc")
            self.codegen.set_signedness(
                out, self.codegen.is_unsigned_value(value) is False
            )
            return out
        if self._is_float_type(value.type) and self._is_float_type(target_type):
            if isinstance(value.type, ir.FloatType) and isinstance(
                target_type, ir.DoubleType
            ):
                return self.builder.fpext(value, target_type, name="fpext")
            if isinstance(value.type, ir.DoubleType) and isinstance(
                target_type, ir.FloatType
            ):
                return self.builder.fptrunc(value, target_type, name="fptrunc")
        if isinstance(value.type, ir.PointerType) and isinstance(
            target_type, ir.PointerType
        ):
            return self.builder.bitcast(value, target_type, name="bitcast")
        # pointer -> int: use ptrtoint (e.g., passing array to untyped param)
        if isinstance(value.type, ir.PointerType) and isinstance(
            target_type, ir.IntType
        ):
            return self.builder.ptrtoint(value, target_type, name="ptrtoint")
        # int -> pointer: use inttoptr (e.g., loading array from i64 storage)
        if isinstance(value.type, ir.IntType) and isinstance(
            target_type, ir.PointerType
        ):
            return self.builder.inttoptr(value, target_type, name="inttoptr")
        # L7 fix: Warn about unhandled type pair instead of silently returning original

        print(
            f"Warning: cast_value unhandled type pair: {value.type} -> {target_type}",
            file=sys.stderr,
        )
        return value

    def _safe_fptosi(self, value: ir.Value, target_type: ir.IntType) -> ir.Value:
        """Safe float-to-int conversion with overflow checking.

        Checks that the float value is within the representable range of the
        target integer type before converting. Out-of-range values cause an error.
        """
        bit_width = target_type.width
        # Calculate min/max values for the target integer type
        # For signed: min = -(2^(n-1)), max = 2^(n-1) - 1
        int_min = -(1 << (bit_width - 1))
        int_max = (1 << (bit_width - 1)) - 1

        # Convert to float for comparison
        float_type = value.type
        min_f = ir.Constant(float_type, float(int_min))
        max_f = ir.Constant(float_type, float(int_max))

        # Check: value >= INT_MIN and value <= INT_MAX
        # Also check for NaN (NaN comparisons return false)
        is_too_small = self.builder.fcmp_ordered("<", value, min_f)
        is_too_large = self.builder.fcmp_ordered(">", value, max_f)
        # Check for NaN: NaN != NaN is true
        is_nan = self.builder.fcmp_unordered("!=", value, value)

        is_invalid = self.builder.or_(is_too_small, is_too_large)
        is_invalid = self.builder.or_(is_invalid, is_nan)

        error_block = self.function.append_basic_block("ftoi_error")
        ok_block = self.function.append_basic_block("ftoi_ok")
        self.builder.cbranch(is_invalid, error_block, ok_block)

        # Error block
        self.builder.position_at_end(error_block)
        error_msg = self.codegen.create_string_constant(
            f"Error: Float value out of range for {bit_width}-bit integer!\n"
        )
        printf = self.codegen.get_printf()
        self.builder.call(printf, [error_msg])
        self.codegen._emit_safety_trap(
            f"Float value out of range for {bit_width}-bit integer"
        )

        # OK block - perform the conversion
        self.builder.position_at_end(ok_block)
        return self.builder.fptosi(value, target_type, name="ftoi")

    def _coerce_numeric_operands(
        self, left: ir.Value, right: ir.Value, unsigned: bool = False
    ) -> tuple[ir.Value, ir.Value, bool]:
        if self._is_float_type(left.type) or self._is_float_type(right.type):
            target = ir.DoubleType()
            left_cast = self.cast_value(left, target)
            right_cast = self.cast_value(right, target)
            return left_cast, right_cast, True
        if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
            if left.type.width == right.type.width:
                return left, right, False
            if left.type.width > right.type.width:
                ext_op = self.builder.zext if unsigned else self.builder.sext
                return left, ext_op(right, left.type, name="ext_rhs"), False
            ext_op = self.builder.zext if unsigned else self.builder.sext
            return ext_op(left, right.type, name="ext_lhs"), right, False
        raise TypeError("Unsupported operand types for numeric operation")

    def ensure_int64(self, value: ir.Value) -> ir.Value:
        if isinstance(value.type, ir.IntType):
            if value.type.width == 64:
                return value
            if value.type.width > 64:
                out = self.builder.trunc(value, ir.IntType(64), name="trunc64")
                self.codegen.set_signedness(
                    out, self.codegen.is_unsigned_value(value) is False
                )
                return out
            ext_op = (
                self.builder.zext
                if self.codegen.is_unsigned_value(value)
                else self.builder.sext
            )
            out = ext_op(value, ir.IntType(64), name="ext64")
            self.codegen.set_signedness(
                out, self.codegen.is_unsigned_value(value) is False
            )
            return out
        # Auto-convert pointers to i64 (enables storing class ptrs in arrays)
        if isinstance(value.type, ir.PointerType):
            return self.builder.ptrtoint(value, ir.IntType(64), name="ptr_to_i64")
        raise TypeError("Array index must be an integer")

    def _compare_strings(self, op: str, left: ir.Value, right: ir.Value) -> ir.Value:
        strcmp = self.codegen.get_strcmp()
        cmp_result = self.builder.call(strcmp, [left, right], name="strcmp")
        zero = ir.Constant(ir.IntType(32), 0)
        if op in {"==", "eq"}:
            return self.builder.icmp_signed("==", cmp_result, zero, name="strcmp_eq")
        if op in {"!=", "ne"}:
            return self.builder.icmp_signed("!=", cmp_result, zero, name="strcmp_ne")
        if op in {"<", "lt"}:
            return self.builder.icmp_signed("<", cmp_result, zero, name="strcmp_lt")
        if op in {"<=", "le"}:
            return self.builder.icmp_signed("<=", cmp_result, zero, name="strcmp_le")
        if op in {">", "gt"}:
            return self.builder.icmp_signed(">", cmp_result, zero, name="strcmp_gt")
        if op in {">=", "ge"}:
            return self.builder.icmp_signed(">=", cmp_result, zero, name="strcmp_ge")
        raise ExprGenError(f"Unsupported string comparison operator: {op}")

    def _get_pow_intrinsic(self) -> ir.Function:
        if self._pow_intrinsic is None:
            pow_type = ir.FunctionType(
                ir.DoubleType(), [ir.DoubleType(), ir.DoubleType()]
            )
            self._pow_intrinsic = ir.Function(
                self.codegen.module, pow_type, name="llvm.pow.f64"
            )
        return self._pow_intrinsic

    # ------------------------------------------------------------------
    # Signedness helpers
    # ------------------------------------------------------------------

    def _is_unsigned_node(self, node) -> bool:
        """Best-effort: variables declared with u* types mark operations as unsigned."""
        if isinstance(node, Variable):
            return self.codegen.var_signedness.get(node.name, True) is False
        return False
