"""
SafetyEmitter - service for LLVM value helpers, arithmetic safety checks,
and bounds/range checks.

Phase A7 extraction from ``CodeGen``.
"""

from __future__ import annotations

from typing import Any, Optional

from llvmlite import ir


class SafetyEmitter:
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
        if is_unsigned:
            out = self.current_builder.urem(left, right, name="urem_proven")
            self.set_signedness(out, False)
            return out
        out = self.current_builder.srem(left, right, name="srem_proven")
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

    def safe_add(self, left: ir.Value, right: ir.Value, is_unsigned: bool) -> ir.Value:
        """Generates safe addition with overflow detection for all integer widths."""
        # Skip overflow checking in unchecked mode for max performance
        if self._unchecked_mode:
            return self.current_builder.add(left, right, name="addtmp")

        # Check all integer types with overflow intrinsics
        if not isinstance(left.type, ir.IntType):
            return self.current_builder.add(left, right, name="addtmp")

        width = left.type.width
        # LLVM overflow intrinsics support widths: 8, 16, 32, 64, 128
        if width in (8, 16, 32, 64, 128):
            return self._safe_add_intrinsic(left, right, width, is_unsigned)

        # For i256+ use manual comparison-based overflow detection
        return self._safe_add_manual(left, right, width, is_unsigned)

    def _safe_add_intrinsic(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Overflow detection via LLVM intrinsics (i8-i128)."""

        # Use LLVM's overflow intrinsics for efficient checking
        prefix = "u" if is_unsigned else "s"
        intrinsic_name = f"llvm.{prefix}add.with.overflow.i{width}"

        # Declare the intrinsic if not already declared
        int_type = ir.IntType(width)
        result_type = ir.LiteralStructType([int_type, ir.IntType(1)])
        func_type = ir.FunctionType(result_type, [int_type, int_type])

        if intrinsic_name not in self.module.globals:
            intrinsic = ir.Function(self.module, func_type, name=intrinsic_name)
        else:
            intrinsic = self.module.globals[intrinsic_name]

        # Call intrinsic
        result = self.current_builder.call(intrinsic, [left, right], name="add_result")
        value = self.current_builder.extract_value(result, 0, name="add_value")
        overflow = self.current_builder.extract_value(result, 1, name="add_overflow")

        # Branch on overflow
        error_block = self.current_function.append_basic_block("add_overflow")
        ok_block = self.current_function.append_basic_block("add_ok")
        self.current_builder.cbranch(overflow, error_block, ok_block)

        # Error block: print and exit
        self.current_builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer overflow in addition!\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer overflow in addition")

        # OK block: return value
        self.current_builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def _safe_add_manual(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Manual overflow detection for wide types (i256+) via comparison."""
        builder = self.current_builder
        value = builder.add(left, right, name="addtmp")
        zero = ir.Constant(ir.IntType(width), 0)

        if is_unsigned:
            # Unsigned: overflow if result < either operand
            overflow = builder.icmp_unsigned("<", value, left, name="add_ovf")
        else:
            # Signed: overflow if (right > 0 && result < left)
            #                  or (right < 0 && result > left)
            right_pos = builder.icmp_signed(">", right, zero, name="rpos")
            res_lt = builder.icmp_signed("<", value, left, name="res_lt")
            pos_ovf = builder.and_(right_pos, res_lt, name="pos_ovf")

            right_neg = builder.icmp_signed("<", right, zero, name="rneg")
            res_gt = builder.icmp_signed(">", value, left, name="res_gt")
            neg_ovf = builder.and_(right_neg, res_gt, name="neg_ovf")

            overflow = builder.or_(pos_ovf, neg_ovf, name="add_ovf")

        error_block = self.current_function.append_basic_block("add_overflow")
        ok_block = self.current_function.append_basic_block("add_ok")
        builder.cbranch(overflow, error_block, ok_block)

        builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer overflow in addition!\n"
        )
        printf = self.get_printf()
        builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer overflow in addition", builder=builder)

        builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def safe_sub(self, left: ir.Value, right: ir.Value, is_unsigned: bool) -> ir.Value:
        """Generates safe subtraction with underflow detection for all integer widths."""
        # Skip underflow checking in unchecked mode for max performance
        if self._unchecked_mode:
            return self.current_builder.sub(left, right, name="subtmp")

        # Check all integer types with overflow intrinsics
        if not isinstance(left.type, ir.IntType):
            return self.current_builder.sub(left, right, name="subtmp")

        width = left.type.width
        # LLVM overflow intrinsics support widths: 8, 16, 32, 64, 128
        if width in (8, 16, 32, 64, 128):
            return self._safe_sub_intrinsic(left, right, width, is_unsigned)

        # For i256+ use manual comparison-based overflow detection
        return self._safe_sub_manual(left, right, width, is_unsigned)

    def _safe_sub_intrinsic(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Underflow detection via LLVM intrinsics (i8-i128)."""

        # Use LLVM's overflow intrinsics for efficient checking
        prefix = "u" if is_unsigned else "s"
        intrinsic_name = f"llvm.{prefix}sub.with.overflow.i{width}"

        # Declare the intrinsic if not already declared
        int_type = ir.IntType(width)
        result_type = ir.LiteralStructType([int_type, ir.IntType(1)])
        func_type = ir.FunctionType(result_type, [int_type, int_type])

        if intrinsic_name not in self.module.globals:
            intrinsic = ir.Function(self.module, func_type, name=intrinsic_name)
        else:
            intrinsic = self.module.globals[intrinsic_name]

        # Call intrinsic
        result = self.current_builder.call(intrinsic, [left, right], name="sub_result")
        value = self.current_builder.extract_value(result, 0, name="sub_value")
        overflow = self.current_builder.extract_value(result, 1, name="sub_overflow")

        # Branch on overflow (underflow)
        error_block = self.current_function.append_basic_block("sub_overflow")
        ok_block = self.current_function.append_basic_block("sub_ok")
        self.current_builder.cbranch(overflow, error_block, ok_block)

        # Error block: print and exit
        self.current_builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer underflow in subtraction!\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer underflow in subtraction")

        # OK block: return value
        self.current_builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def _safe_sub_manual(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Manual underflow detection for wide types (i256+) via comparison."""
        builder = self.current_builder
        value = builder.sub(left, right, name="subtmp")
        zero = ir.Constant(ir.IntType(width), 0)

        if is_unsigned:
            # Unsigned: underflow if right > left
            overflow = builder.icmp_unsigned(">", right, left, name="sub_ovf")
        else:
            # Signed: underflow if (right > 0 && result > left)
            #                   or (right < 0 && result < left)
            right_pos = builder.icmp_signed(">", right, zero, name="rpos")
            res_gt = builder.icmp_signed(">", value, left, name="res_gt")
            pos_ovf = builder.and_(right_pos, res_gt, name="pos_ovf")

            right_neg = builder.icmp_signed("<", right, zero, name="rneg")
            res_lt = builder.icmp_signed("<", value, left, name="res_lt")
            neg_ovf = builder.and_(right_neg, res_lt, name="neg_ovf")

            overflow = builder.or_(pos_ovf, neg_ovf, name="sub_ovf")

        error_block = self.current_function.append_basic_block("sub_overflow")
        ok_block = self.current_function.append_basic_block("sub_ok")
        builder.cbranch(overflow, error_block, ok_block)

        builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer underflow in subtraction!\n"
        )
        printf = self.get_printf()
        builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer underflow in subtraction", builder=builder)

        builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def safe_mul(self, left: ir.Value, right: ir.Value, is_unsigned: bool) -> ir.Value:
        """Generates safe multiplication with overflow detection for all integer widths."""
        # Skip overflow checking in unchecked mode for max performance
        if self._unchecked_mode:
            return self.current_builder.mul(left, right, name="multmp")

        # Check all integer types with overflow intrinsics
        if not isinstance(left.type, ir.IntType):
            return self.current_builder.mul(left, right, name="multmp")

        width = left.type.width
        # LLVM overflow intrinsics support widths: 8, 16, 32, 64, 128
        if width in (8, 16, 32, 64, 128):
            return self._safe_mul_intrinsic(left, right, width, is_unsigned)

        # For i256+ use widen-and-compare overflow detection
        return self._safe_mul_manual(left, right, width, is_unsigned)

    def _safe_mul_intrinsic(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Overflow detection via LLVM intrinsics (i8-i128)."""

        # Use LLVM's overflow intrinsics
        prefix = "u" if is_unsigned else "s"
        intrinsic_name = f"llvm.{prefix}mul.with.overflow.i{width}"

        int_type = ir.IntType(width)
        result_type = ir.LiteralStructType([int_type, ir.IntType(1)])
        func_type = ir.FunctionType(result_type, [int_type, int_type])

        if intrinsic_name not in self.module.globals:
            intrinsic = ir.Function(self.module, func_type, name=intrinsic_name)
        else:
            intrinsic = self.module.globals[intrinsic_name]

        result = self.current_builder.call(intrinsic, [left, right], name="mul_result")
        value = self.current_builder.extract_value(result, 0, name="mul_value")
        overflow = self.current_builder.extract_value(result, 1, name="mul_overflow")

        error_block = self.current_function.append_basic_block("mul_overflow")
        ok_block = self.current_function.append_basic_block("mul_ok")
        self.current_builder.cbranch(overflow, error_block, ok_block)

        self.current_builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer overflow in multiplication!\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer overflow in multiplication")

        self.current_builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def _safe_mul_manual(
        self, left: ir.Value, right: ir.Value, width: int, is_unsigned: bool
    ) -> ir.Value:
        """Manual overflow detection for wide types (i256+) via widen-and-truncate."""
        builder = self.current_builder
        int_type = ir.IntType(width)
        wide_type = ir.IntType(width * 2)

        # Widen both operands to 2x width, multiply, then check if it fits
        if is_unsigned:
            wide_left = builder.zext(left, wide_type, name="wleft")
            wide_right = builder.zext(right, wide_type, name="wright")
        else:
            wide_left = builder.sext(left, wide_type, name="wleft")
            wide_right = builder.sext(right, wide_type, name="wright")

        wide_result = builder.mul(wide_left, wide_right, name="wmul")

        # Truncate back to original width
        value = builder.trunc(wide_result, int_type, name="multmp")

        # Check: re-extend and compare to wide result
        if is_unsigned:
            check = builder.zext(value, wide_type, name="check")
        else:
            check = builder.sext(value, wide_type, name="check")

        overflow = builder.icmp_unsigned("!=", check, wide_result, name="mul_ovf")

        error_block = self.current_function.append_basic_block("mul_overflow")
        ok_block = self.current_function.append_basic_block("mul_ok")
        builder.cbranch(overflow, error_block, ok_block)

        builder.position_at_end(error_block)
        error_msg = self.create_string_constant(
            "Error: Integer overflow in multiplication!\n"
        )
        printf = self.get_printf()
        builder.call(printf, [error_msg])
        self._emit_safety_trap("Integer overflow in multiplication", builder=builder)

        builder.position_at_end(ok_block)
        self.set_signedness(value, not is_unsigned)
        return value

    def to_bool(self, value: ir.Value) -> ir.Value:
        """Convert any supported LLVM value to a boolean i1."""
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

    def cast_value(self, value: ir.Value, target_type: ir.Type) -> ir.Value:
        """Delegate to expression generator's casting logic."""
        return self.expr_generator.cast_value(
            value, target_type, unsigned=self.is_unsigned_value(value)
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
