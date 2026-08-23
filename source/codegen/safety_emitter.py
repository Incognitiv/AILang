"""
SafetyEmitter - service for LLVM value helpers, arithmetic safety checks,
and bounds/range checks.

Phase A7 extraction from ``CodeGen``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from llvmlite import ir
from transpiler.codegen_int_ranges import (
    expr_int_range,
    range_fits_signed_width,
    range_is_positive,
)

from .safety_arithmetic import SafetyArithmeticMixin


class SafetyEmitter(SafetyArithmeticMixin):
    """Runtime-safe value helpers delegated from ``CodeGen``."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def is_unsigned_value(self, value: ir.Value) -> bool:
        """Best-effort: infer unsigned from SSA name mapped to declared vars."""
        value_name = getattr(value, "name", "")
        if value_name:
            sign = self.value_signedness.get(value_name)
            if sign is not None:
                return sign is False
        name = value_name
        if name:
            base = name[:-4] if name.endswith("_val") else name
            if base in self.var_signedness:
                return self.var_signedness.get(base, True) is False
        return False

    def set_signedness(self, value: ir.Value, is_signed: bool) -> None:
        """Record signedness for an SSA value."""
        value_name = getattr(value, "name", "")
        if value_name:
            self.value_signedness[value_name] = is_signed

    # ========================================================================
    # Helpers and Utilities
    # ========================================================================

    def safe_division(
        self, left: ir.Value, right: ir.Value, is_float: bool, is_unsigned: bool
    ) -> ir.Value:
        """Generates safe division with checks for zero and INT_MIN/-1 overflow."""
        if is_float:
            # Check for float division by zero (0.0 / 0.0 produces NaN silently)
            zero_f = ir.Constant(right.type, 0.0)
            is_zero = self.current_builder.fcmp_ordered("==", right, zero_f)
            error_block = self.current_function.append_basic_block("fdiv_by_zero")
            ok_block = self.current_function.append_basic_block("fdiv_ok")
            self.current_builder.cbranch(is_zero, error_block, ok_block)

            self.current_builder.position_at_end(error_block)
            error_msg = self.create_string_constant("Error: Float division by zero!\n")
            printf = self.get_printf()
            self.current_builder.call(printf, [error_msg])
            self._emit_safety_trap("Float division by zero")

            self.current_builder.position_at_end(ok_block)
            return self.current_builder.fdiv(left, right, name="fdivtmp")

        # Integer division: check for zero
        zero = ir.Constant(right.type, 0)
        cmp_op = "=="
        is_zero = (
            self.current_builder.icmp_unsigned(cmp_op, right, zero)
            if is_unsigned
            else self.current_builder.icmp_signed(cmp_op, right, zero)
        )

        error_block = self.current_function.append_basic_block("div_by_zero")
        ok_block = self.current_function.append_basic_block("div_ok")

        self.current_builder.cbranch(is_zero, error_block, ok_block)

        self.current_builder.position_at_end(error_block)
        error_msg = self.create_string_constant("Error: Division by zero!\n")
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Division by zero")

        self.current_builder.position_at_end(ok_block)

        # For signed division: check for INT_MIN / -1 overflow
        # This is undefined behavior in C because -INT_MIN > INT_MAX
        if not is_unsigned:
            bit_width = left.type.width
            int_min = -(1 << (bit_width - 1))  # e.g., -9223372036854775808 for i64
            min_val = ir.Constant(left.type, int_min)
            neg_one = ir.Constant(right.type, -1)

            is_min = self.current_builder.icmp_signed("==", left, min_val)
            is_neg_one = self.current_builder.icmp_signed("==", right, neg_one)
            is_overflow = self.current_builder.and_(is_min, is_neg_one)

            overflow_block = self.current_function.append_basic_block("div_overflow")
            safe_block = self.current_function.append_basic_block("div_safe")
            self.current_builder.cbranch(is_overflow, overflow_block, safe_block)

            self.current_builder.position_at_end(overflow_block)
            overflow_msg = self.create_string_constant(
                "Error: Integer overflow (INT_MIN / -1)!\n"
            )
            self.current_builder.call(printf, [overflow_msg])
            self._emit_safety_trap("Integer overflow (INT_MIN / -1)")

            self.current_builder.position_at_end(safe_block)
            res = self.current_builder.sdiv(left, right, name="sdivtmp")
            self.set_signedness(res, True)
            return res

        res = self.current_builder.udiv(left, right, name="udivtmp")
        self.set_signedness(res, False)
        return res

    def safe_modulo(
        self, left: ir.Value, right: ir.Value, is_float: bool, is_unsigned: bool
    ) -> ir.Value:
        """Generates safe modulo with checks for zero and INT_MIN%-1 overflow."""
        if is_float:
            return self.current_builder.frem(left, right, name="fremtmp")

        zero = ir.Constant(right.type, 0)
        cmp_op = "=="
        is_zero = (
            self.current_builder.icmp_unsigned(cmp_op, right, zero)
            if is_unsigned
            else self.current_builder.icmp_signed(cmp_op, right, zero)
        )

        error_block = self.current_function.append_basic_block("mod_by_zero")
        ok_block = self.current_function.append_basic_block("mod_ok")

        self.current_builder.cbranch(is_zero, error_block, ok_block)

        self.current_builder.position_at_end(error_block)
        error_msg = self.create_string_constant("Error: Modulo by zero!\n")
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Modulo by zero")

        self.current_builder.position_at_end(ok_block)

        # For signed modulo: check for INT_MIN % -1 overflow
        if not is_unsigned:
            bit_width = left.type.width
            int_min = -(1 << (bit_width - 1))
            min_val = ir.Constant(left.type, int_min)
            neg_one = ir.Constant(right.type, -1)

            is_min = self.current_builder.icmp_signed("==", left, min_val)
            is_neg_one = self.current_builder.icmp_signed("==", right, neg_one)
            is_overflow = self.current_builder.and_(is_min, is_neg_one)

            overflow_block = self.current_function.append_basic_block("mod_overflow")
            safe_block = self.current_function.append_basic_block("mod_safe")
            self.current_builder.cbranch(is_overflow, overflow_block, safe_block)

            self.current_builder.position_at_end(overflow_block)
            overflow_msg = self.create_string_constant(
                "Error: Integer overflow (INT_MIN %% -1)!\n"
            )
            self.current_builder.call(printf, [overflow_msg])
            self._emit_safety_trap("Integer overflow (INT_MIN % -1)")

            self.current_builder.position_at_end(safe_block)
            res = self.current_builder.srem(left, right, name="sremtmp")
            self.set_signedness(res, True)
            return res

        res = self.current_builder.urem(left, right, name="uremtmp")
        self.set_signedness(res, False)
        return res

    def _proven_no_overflow_for_node(
        self,
        node: Any,
        left: ir.Value,
        is_unsigned: bool,
    ) -> bool:
        facts = getattr(self, "range_facts", None)
        if facts is None:
            return False
        if not isinstance(left.type, ir.IntType):
            return False
        scope = getattr(self, "_current_function_name", None)
        # Phase P6 hardening: no-wrap flags require node-local proof snapshots.
        # If a snapshot is missing, keep safe_* intrinsic fallback.
        if not facts.has_expr_scope_snapshot(node, scope):
            return False
        try:
            return facts.can_prove_no_overflow_for_int(
                node,
                scope,
                bit_width=left.type.width,
                is_unsigned=is_unsigned,
            )
        except Exception:
            return False

    def try_proven_int_arithmetic(
        self,
        node: Any,
        left: ir.Value,
        right: ir.Value,
        op: str,
        is_unsigned: bool,
    ) -> Optional[ir.Value]:
        """Emit raw int arithmetic with no-wrap flags when overflow is proven impossible."""
        if self._unchecked_mode:
            return None
        if not isinstance(left.type, ir.IntType) or not isinstance(
            right.type, ir.IntType
        ):
            return None
        if left.type != right.type:
            return None
        local_range = expr_int_range(self, node)
        if not is_unsigned and range_fits_signed_width(local_range, left.type.width):
            flags = ("nsw",)
            if op in {"+", "plus"}:
                out = self.current_builder.add(
                    left, right, name="add_range_proven", flags=flags
                )
                self.set_signedness(out, True)
                return out
            if op in {"-", "minus"}:
                out = self.current_builder.sub(
                    left, right, name="sub_range_proven", flags=flags
                )
                self.set_signedness(out, True)
                return out
            if op in {"*", "star"}:
                out = self.current_builder.mul(
                    left, right, name="mul_range_proven", flags=flags
                )
                self.set_signedness(out, True)
                return out
        if not self._proven_no_overflow_for_node(node, left, is_unsigned):
            return None
        flags = ("nuw",) if is_unsigned else ("nsw",)
        if op in {"+", "plus"}:
            out = self.current_builder.add(left, right, name="add_proven", flags=flags)
            self.set_signedness(out, not is_unsigned)
            return out
        if op in {"-", "minus"}:
            out = self.current_builder.sub(left, right, name="sub_proven", flags=flags)
            self.set_signedness(out, not is_unsigned)
            return out
        if op in {"*", "star"}:
            out = self.current_builder.mul(left, right, name="mul_proven", flags=flags)
            self.set_signedness(out, not is_unsigned)
            return out
        return None

    def try_proven_modulo(
        self,
        node: Any,
        left: ir.Value,
        right: ir.Value,
        *,
        is_float: bool,
        is_unsigned: bool,
    ) -> Optional[ir.Value]:
        """Emit raw modulo when range facts prove runtime safety checks redundant."""
        if self._unchecked_mode or is_float:
            return None
        if not isinstance(left.type, ir.IntType) or not isinstance(
            right.type, ir.IntType
        ):
            return None
        if left.type != right.type:
            return None
        local_lhs_range = expr_int_range(self, node.left)
        local_rhs_range = expr_int_range(self, node.right)
        if range_is_positive(local_rhs_range):
            reduced = self._try_single_subtract_modulo(
                left,
                right,
                local_lhs_range,
                local_rhs_range,
                is_unsigned=is_unsigned,
            )
            if reduced is not None:
                return reduced
            if is_unsigned or (local_lhs_range is not None and local_lhs_range[0] >= 0):
                out = self.current_builder.urem(left, right, name="urem_proven")
                self.set_signedness(out, not is_unsigned)
                return out
            out = self.current_builder.srem(left, right, name="srem_proven")
            self.set_signedness(out, True)
            return out
        facts = getattr(self, "range_facts", None)
        if facts is None:
            return None
        scope = getattr(self, "_current_function_name", None)
        if not facts.has_expr_scope_snapshot(node, scope):
            return None
        try:
            proven = facts.can_prove_safe_modulo(
                node,
                scope,
                bit_width=left.type.width,
                is_unsigned=is_unsigned,
            )
        except Exception:
            proven = False
        if not proven:
            return None
        if is_unsigned or (local_lhs_range is not None and local_lhs_range[0] >= 0):
            out = self.current_builder.urem(left, right, name="urem_proven")
            self.set_signedness(out, not is_unsigned)
            return out
        out = self.current_builder.srem(left, right, name="srem_proven")
        self.set_signedness(out, True)
        return out

    def _try_single_subtract_modulo(
        self,
        left: ir.Value,
        right: ir.Value,
        lhs_range: object,
        rhs_range: object,
        *,
        is_unsigned: bool,
    ) -> Optional[ir.Value]:
        """Lower `x % m` to at most one subtract when ranges prove it safe."""
        lhs_pair = self._range_pair(lhs_range)
        rhs_pair = self._range_pair(rhs_range)
        if lhs_pair is None or rhs_pair is None:
            return None
        lhs_low, lhs_high = lhs_pair
        rhs_low, rhs_high = rhs_pair
        if lhs_low < 0 or rhs_low <= 0 or rhs_low != rhs_high:
            return None
        modulus = rhs_low
        if lhs_high >= modulus * 2:
            return None
        ge_mod = self.current_builder.icmp_unsigned(
            ">=", left, right, name="mod_ge_const"
        )
        reduced = self.current_builder.sub(left, right, name="mod_sub_once")
        out = self.current_builder.select(
            ge_mod, reduced, left, name="mod_range_select"
        )
        self.set_signedness(out, not is_unsigned)
        return out

    def _range_pair(self, value: object) -> Optional[tuple[int, int]]:
        if not isinstance(value, Sequence) or len(value) < 2:
            return None
        return int(value[0]), int(value[1])

    def try_proven_division(
        self,
        node: Any,
        left: ir.Value,
        right: ir.Value,
        *,
        is_float: bool,
        is_unsigned: bool,
    ) -> Optional[ir.Value]:
        """Emit raw division when range facts prove runtime checks redundant."""
        if self._unchecked_mode or is_float:
            return None
        if not isinstance(left.type, ir.IntType) or not isinstance(
            right.type, ir.IntType
        ):
            return None
        if left.type != right.type:
            return None
        local_rhs_range = expr_int_range(self, node.right)
        if range_is_positive(local_rhs_range):
            if is_unsigned:
                out = self.current_builder.udiv(left, right, name="udiv_proven")
                self.set_signedness(out, False)
                return out
            out = self.current_builder.sdiv(left, right, name="sdiv_proven")
            self.set_signedness(out, True)
            return out
        facts = getattr(self, "range_facts", None)
        if facts is None:
            return None
        scope = getattr(self, "_current_function_name", None)
        if not facts.has_expr_scope_snapshot(node, scope):
            return None
        try:
            proven = facts.can_prove_safe_division(
                node,
                scope,
                bit_width=left.type.width,
                is_unsigned=is_unsigned,
            )
        except Exception:
            proven = False
        if not proven:
            return None
        if is_unsigned:
            out = self.current_builder.udiv(left, right, name="udiv_proven")
            self.set_signedness(out, False)
            return out
        out = self.current_builder.sdiv(left, right, name="sdiv_proven")
        self.set_signedness(out, True)
        return out

    def _emit_range_error(
        self,
        var_name: str,
        value: ir.Value,
        low: ir.Value,
        high: ir.Value,
    ) -> None:
        """Emit range error message and exit."""
        # Format: "Range error: var_name = value not in low..high\n"
        fmt = self.create_string_constant(
            f"Range error: {var_name} = %lld not in %lld..%lld\\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [fmt, value, low, high])
        self._emit_safety_trap(f"Range error: {var_name} value out of range")

    def _emit_string_bounds_error(self, index: ir.Value, length: ir.Value) -> None:
        """Emit string bounds error message and exit."""
        fmt = self.create_string_constant(
            "Error: string index %lld out of bounds [0, %lld)\\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [fmt, index, length])
        self._emit_safety_trap("String index out of bounds")

    def to_bool(self, value: ir.Value) -> ir.Value:
        """Convert any supported LLVM value to a boolean i1."""
        if self.is_bigint_type(value.type):
            zero = self.current_builder.call(
                self._get_bigint_from_int(), [ir.Constant(ir.IntType(64), 0)], name="bigint_bool_zero"
            )
            cmpv = self.current_builder.call(self._get_bigint_cmp(), [value, zero], name="bigint_bool_cmp")
            self.current_builder.call(self._get_bigint_free(), [zero])
            return self.current_builder.icmp_signed("!=", cmpv, ir.Constant(ir.IntType(64), 0), name="tobool_bigint")
        if isinstance(value.type, ir.IntType):
            if value.type.width == 1:
                return value
            zero = ir.Constant(value.type, 0)
            if self.is_unsigned_value(value):
                return self.current_builder.icmp_unsigned(
                    "!=", value, zero, name="tobool_int_u"
                )
            return self.current_builder.icmp_signed(
                "!=", value, zero, name="tobool_int"
            )
        if isinstance(value.type, (ir.FloatType, ir.DoubleType)):
            zero = ir.Constant(value.type, 0.0)
            return self.current_builder.fcmp_ordered(
                "!=", value, zero, name="tobool_float"
            )
        if isinstance(value.type, ir.PointerType):
            null_ptr = ir.Constant(value.type, None)
            return self.current_builder.icmp_unsigned(
                "!=", value, null_ptr, name="tobool_ptr"
            )
        raise TypeError("Unsupported type for boolean conversion")

    def cast_value(
        self,
        value: ir.Value,
        target_type: ir.Type,
        *,
        target_unsigned: bool | None = None,
    ) -> ir.Value:
        """Delegate to expression generator's checked casting logic."""
        return self.expr_generator.cast_value(
            value,
            target_type,
            unsigned=self.is_unsigned_value(value),
            target_unsigned=target_unsigned,
        )

    def ensure_int64(self, value: ir.Value) -> ir.Value:
        """Ensure value is a 64-bit integer (sign-extended if needed)."""
        return self.expr_generator.ensure_int64(value)

    def default_value(self, llvm_type: ir.Type) -> ir.Constant:
        """Return a zero-equivalent constant for the given LLVM type."""
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)
        if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(llvm_type, 0.0)
        if isinstance(llvm_type, ir.PointerType):
            return ir.Constant(llvm_type, None)
        if isinstance(llvm_type, (ir.ArrayType, ir.LiteralStructType)):
            return ir.Constant(llvm_type, None)
        raise TypeError(f"Unsupported default value for type: {llvm_type}")

    def check_bounds(self, index: ir.Value, length: int):
        """Generates array bounds check with compile-time known length."""
        len_val = ir.Constant(index.type, length)
        self.check_bounds_dynamic(index, len_val)

    def check_bounds_dynamic(self, index: ir.Value, length: ir.Value):
        """Generates array bounds check with runtime length.

        Checks: 0 <= index < length
        Exits with error message if out of bounds.
        """
        # Ensure both are same type
        if (
            index.type != length.type
            and isinstance(length.type, ir.IntType)
            and isinstance(index.type, ir.IntType)
        ):
            # Extend to match
            if length.type.width < index.type.width:
                length = self.current_builder.zext(length, index.type)
            else:
                index = self.current_builder.zext(index, length.type)

        zero = ir.Constant(index.type, 0)

        if self.is_unsigned_value(index):
            lower_check = self.current_builder.icmp_unsigned(">=", index, zero)
            upper_check = self.current_builder.icmp_unsigned("<", index, length)
        else:
            lower_check = self.current_builder.icmp_signed(">=", index, zero)
            upper_check = self.current_builder.icmp_signed("<", index, length)
        in_bounds = self.current_builder.and_(lower_check, upper_check)

        error_block = self.current_function.append_basic_block("bounds_error")
        ok_block = self.current_function.append_basic_block("bounds_ok")

        self.current_builder.cbranch(in_bounds, ok_block, error_block)

        self.current_builder.position_at_end(error_block)
        # Print error and abort (consistent with overflow/div-by-zero checks)
        error_msg = self.create_string_constant("Error: Array index out of bounds!\n")
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Array index out of bounds")

        self.current_builder.position_at_end(ok_block)
