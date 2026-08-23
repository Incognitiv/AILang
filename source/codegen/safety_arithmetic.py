"""Checked integer arithmetic helpers for LLVM code generation."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class SafetyArithmeticMixin:
    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

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
