"""Extracted responsibilities for :class:`ExprOpsEmitter`."""

from __future__ import annotations

from parser.ast import BinaryOp, TernaryOp
from typing import Any

from llvmlite import ir
from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)
from transpiler.codegen_int_ranges import expr_int_range
from transpiler.expr_common import ExprGenError


class ExprOpsBinaryMixin:
    def _visit_bigint_binary(self: Any, node: BinaryOp) -> ir.Value:
        op = node.op
        op_lower = op.lower()
        if op_lower in {
            "shl",
            "<<",
            "lshift",
            "shr",
            ">>",
            "rshift",
            "ushr",
            "**",
            "^",
            "power",
        }:
            left, own_left = self._bigint_operand(node.left)
            if op_lower == "ushr":
                if own_left:
                    self.builder.call(self.codegen._get_bigint_free(), [left])
                raise ExprGenError(
                    "logical right shift 'ushr' is undefined for unbounded integers; use >>"
                )
            count, owned_count = self._bigint_nonnegative_i64(node.right)
            if op_lower in {"**", "^", "power"}:
                result = self.builder.call(
                    self.codegen._get_bigint_pow(), [left, count], name="bigint_pow"
                )
            else:
                fn = (
                    self.codegen._get_bigint_shl()
                    if op_lower in {"shl", "<<", "lshift"}
                    else self.codegen._get_bigint_shr()
                )
                result = self.builder.call(fn, [left, count], name="bigint_shift")
            if own_left:
                self.builder.call(self.codegen._get_bigint_free(), [left])
            if owned_count is not None:
                self.builder.call(self.codegen._get_bigint_free(), [owned_count])
            return result

        left, own_left = self._bigint_operand(node.left)
        right, own_right = self._bigint_operand(node.right)
        binary_fns = {
            "+": self.codegen._get_bigint_add,
            "plus": self.codegen._get_bigint_add,
            "-": self.codegen._get_bigint_sub,
            "minus": self.codegen._get_bigint_sub,
            "*": self.codegen._get_bigint_mul,
            "star": self.codegen._get_bigint_mul,
            "/": self.codegen._get_bigint_div,
            "slash": self.codegen._get_bigint_div,
            "%": self.codegen._get_bigint_mod,
            "mod": self.codegen._get_bigint_mod,
            "&": self.codegen._get_bigint_and,
            "ampersand": self.codegen._get_bigint_and,
            "|": self.codegen._get_bigint_or,
            "pipe": self.codegen._get_bigint_or,
            "band": self.codegen._get_bigint_and,
            "bor": self.codegen._get_bigint_or,
            "bxor": self.codegen._get_bigint_xor,
        }
        if op in {"AND", "OR", "XOR"}:
            fn_getter = {
                "AND": self.codegen._get_bigint_and,
                "OR": self.codegen._get_bigint_or,
                "XOR": self.codegen._get_bigint_xor,
            }[op]
            result = self.builder.call(
                fn_getter(), [left, right], name="bigint_bitwise"
            )
        elif op_lower in binary_fns:
            result = self.builder.call(
                binary_fns[op_lower](), [left, right], name="bigint_op"
            )
        elif op_lower in {
            "==",
            "eq",
            "!=",
            "ne",
            "<",
            "lt",
            "<=",
            "le",
            ">",
            "gt",
            ">=",
            "ge",
        }:
            cmpv = self.builder.call(
                self.codegen._get_bigint_cmp(), [left, right], name="bigint_cmp"
            )
            zero = ir.Constant(ir.IntType(64), 0)
            pred = {
                "==": "==",
                "eq": "==",
                "!=": "!=",
                "ne": "!=",
                "<": "<",
                "lt": "<",
                "<=": "<=",
                "le": "<=",
                ">": ">",
                "gt": ">",
                ">=": ">=",
                "ge": ">=",
            }[op_lower]
            result = self.builder.icmp_signed(pred, cmpv, zero, name="bigint_cmp_bool")
        else:
            if own_left:
                self.builder.call(self.codegen._get_bigint_free(), [left])
            if own_right:
                self.builder.call(self.codegen._get_bigint_free(), [right])
            raise ExprGenError(f"operator {node.op!r} is not supported for unbounded")
        if own_left:
            self.builder.call(self.codegen._get_bigint_free(), [left])
        if own_right:
            self.builder.call(self.codegen._get_bigint_free(), [right])
        return result

    def visit_BinaryOp(self: Any, node: BinaryOp):
        op = node.op  # Keep original case for gate operators
        op_lower = op.lower()

        if self._expr_is_unbounded(node.left) or self._expr_is_unbounded(node.right):
            return self._visit_bigint_binary(node)

        if op_lower in {"+", "plus"}:
            fused = self._try_emit_literal_str_concat(node)
            if fused is not None:
                return fused

        left = self.generate_expr(node.left)

        # Logical operators must decide whether to evaluate the right operand
        # before generating its IR.  Generating ``right`` eagerly here and
        # again in the RHS block duplicates effects and breaks short-circuit
        # semantics.  Uppercase AND/OR remain the bitwise gate operators
        # handled below.
        if op != "AND" and op_lower in {"and", "&&"}:
            left_bool = self.codegen.to_bool(left)
            and_true_block = self.codegen.current_function.append_basic_block("and_rhs")
            and_merge_block = self.codegen.current_function.append_basic_block(
                "and_merge"
            )
            entry_block = self.builder.block
            self.builder.cbranch(left_bool, and_true_block, and_merge_block)

            self.builder.position_at_end(and_true_block)
            right_lazy = self.generate_expr(node.right)
            right_bool = self.codegen.to_bool(right_lazy)
            rhs_exit_block = self.builder.block
            self.builder.branch(and_merge_block)

            self.builder.position_at_end(and_merge_block)
            phi = self.builder.phi(ir.IntType(1), name="and_sc")
            phi.add_incoming(ir.Constant(ir.IntType(1), 0), entry_block)
            phi.add_incoming(right_bool, rhs_exit_block)
            return phi

        if op != "OR" and op_lower in {"or", "||"}:
            left_bool = self.codegen.to_bool(left)
            or_false_block = self.codegen.current_function.append_basic_block("or_rhs")
            or_merge_block = self.codegen.current_function.append_basic_block(
                "or_merge"
            )
            entry_block = self.builder.block
            self.builder.cbranch(left_bool, or_merge_block, or_false_block)

            self.builder.position_at_end(or_false_block)
            right_lazy = self.generate_expr(node.right)
            right_bool = self.codegen.to_bool(right_lazy)
            rhs_exit_block = self.builder.block
            self.builder.branch(or_merge_block)

            self.builder.position_at_end(or_merge_block)
            phi = self.builder.phi(ir.IntType(1), name="or_sc")
            phi.add_incoming(ir.Constant(ir.IntType(1), 1), entry_block)
            phi.add_incoming(right_bool, rhs_exit_block)
            return phi

        right = self.generate_expr(node.right)

        # Signedness is a language property, not an LLVM type property.  Use
        # both AST declarations and the SSA metadata produced by calls/casts;
        # otherwise a u32-returning function such as raw peek32 can be sext'ed
        # to -1 when its top bit is set.
        left_unsigned = self._is_unsigned_node(
            node.left
        ) or self.codegen.is_unsigned_value(left)
        right_unsigned = self._is_unsigned_node(
            node.right
        ) or self.codegen.is_unsigned_value(right)
        use_unsigned = left_unsigned or right_unsigned

        if op_lower in {"+", "plus"}:
            # Check if this is string concatenation
            left_is_str = self._is_string_pointer(left)
            right_is_str = self._is_string_pointer(right)

            if left_is_str and right_is_str:
                # Both are strings - concatenate
                return self.codegen.generate_string_concat(left, right)
            if left_is_str or right_is_str:
                # One is string, one isn't - this is likely a missing type annotation
                # Try to treat non-pointer operand as string if it's a load from i8*
                if (
                    left_is_str
                    and isinstance(right.type, ir.IntType)
                    and right.type.width == 8
                ):
                    # Right operand is i8 - might be a parameter without type annotation
                    # Create a single-char string from it
                    right = self._byte_to_string(right)
                    return self.codegen.generate_string_concat(left, right)
                if (
                    right_is_str
                    and isinstance(left.type, ir.IntType)
                    and left.type.width == 8
                ):
                    left = self._byte_to_string(left)
                    return self.codegen.generate_string_concat(left, right)
                raise TypeError(
                    f"String concatenation requires string operands. "
                    f"Got {left.type} and {right.type}. "
                    "Did you forget to add type annotation 'name: string'?"
                )

        if self._both_strings(left, right):
            return self._compare_strings(op_lower, left, right)

        # Handle char_at() result (i64 char code) compared with string literal:
        # Convert the string to its first byte's ord value for integer comparison.
        if op_lower in {"==", "!=", "<", ">", "<=", ">="}:
            left_is_str = self._is_string_pointer(left)
            right_is_str = self._is_string_pointer(right)
            if left_is_str and isinstance(right.type, ir.IntType):
                # String on left, int on right → convert string to ord
                first_byte = self.builder.load(left, name="str_first_byte")
                left = self.builder.zext(first_byte, right.type, name="str_as_int")
            elif right_is_str and isinstance(left.type, ir.IntType):
                # Int on left, string on right → convert string to ord
                first_byte = self.builder.load(right, name="str_first_byte")
                right = self.builder.zext(first_byte, left.type, name="str_as_int")

        left, right, is_float = self._coerce_numeric_operands(
            left,
            right,
            use_unsigned,
            left_node=node.left,
            right_node=node.right,
        )

        # Integer Safety 10/10: Check for compile-time overflow on constants
        if not is_float and isinstance(left.type, ir.IntType):
            self._check_constant_overflow(left, right, op_lower, left.type.width)

        if op_lower in {"+", "plus"}:
            if is_float:
                return self.builder.fadd(left, right, name="fadd")
            literal_reason = neutral_int_arithmetic_safe(node)
            if literal_reason is None:
                literal_reason = literal_int_arithmetic_safe(
                    node,
                    bit_width=left.type.width,
                    is_unsigned=use_unsigned,
                )
            if literal_reason is not None:
                res = self.builder.add(left, right, name="add_identity")
                self.codegen.set_signedness(res, not use_unsigned)
                return res
            proven = self.codegen.try_proven_int_arithmetic(
                node, left, right, op_lower, use_unsigned
            )
            if proven is not None:
                return proven
            # Use safe_add for overflow detection on i64
            res = self.codegen.safe_add(left, right, is_unsigned=use_unsigned)
            return res
        if op_lower in {"-", "minus"}:
            if is_float:
                return self.builder.fsub(left, right, name="fsub")
            literal_reason = neutral_int_arithmetic_safe(node)
            if literal_reason is None:
                literal_reason = literal_int_arithmetic_safe(
                    node,
                    bit_width=left.type.width,
                    is_unsigned=use_unsigned,
                )
            if literal_reason is not None:
                res = self.builder.sub(left, right, name="sub_identity")
                self.codegen.set_signedness(res, not use_unsigned)
                return res
            proven = self.codegen.try_proven_int_arithmetic(
                node, left, right, op_lower, use_unsigned
            )
            if proven is not None:
                return proven
            # Use safe_sub for underflow detection on i64
            res = self.codegen.safe_sub(left, right, is_unsigned=use_unsigned)
            return res
        if op_lower in {"*", "star"}:
            if is_float:
                return self.builder.fmul(left, right, name="fmul")
            literal_reason = neutral_int_arithmetic_safe(node)
            if literal_reason is None:
                literal_reason = literal_int_arithmetic_safe(
                    node,
                    bit_width=left.type.width,
                    is_unsigned=use_unsigned,
                )
            if literal_reason is not None:
                res = self.builder.mul(left, right, name="mul_identity")
                self.codegen.set_signedness(res, not use_unsigned)
                return res
            proven = self.codegen.try_proven_int_arithmetic(
                node, left, right, op_lower, use_unsigned
            )
            if proven is not None:
                return proven
            # Use safe_mul for overflow detection on i64
            res = self.codegen.safe_mul(left, right, is_unsigned=use_unsigned)
            return res
        if op_lower in {"/", "slash"}:
            if not is_float and positive_int_literal(node.right):
                if use_unsigned:
                    res = self.builder.udiv(left, right, name="udiv_proven")
                    self.codegen.set_signedness(res, False)
                    return res
                res = self.builder.sdiv(left, right, name="sdiv_proven")
                self.codegen.set_signedness(res, True)
                return res
            proven = self.codegen.try_proven_division(
                node,
                left,
                right,
                is_float=is_float,
                is_unsigned=use_unsigned,
            )
            if proven is not None:
                return proven
            res = self.codegen.safe_division(
                left, right, is_float=is_float, is_unsigned=use_unsigned
            )
            if not is_float:
                self.codegen.set_signedness(res, not use_unsigned)
            return res
        if op_lower in {"%", "mod"}:
            if not is_float and positive_int_literal(node.right):
                left_range = expr_int_range(self.codegen, node.left)
                if use_unsigned or (left_range is not None and left_range[0] >= 0):
                    res = self.builder.urem(left, right, name="urem_proven")
                    self.codegen.set_signedness(res, not use_unsigned)
                    return res
                res = self.builder.srem(left, right, name="srem_proven")
                self.codegen.set_signedness(res, True)
                return res
            proven = self.codegen.try_proven_modulo(
                node,
                left,
                right,
                is_float=is_float,
                is_unsigned=use_unsigned,
            )
            if proven is not None:
                return proven
            res = self.codegen.safe_modulo(
                left, right, is_float=is_float, is_unsigned=use_unsigned
            )
            if not is_float:
                self.codegen.set_signedness(res, not use_unsigned)
            return res

        # Logic Gate operators FIRST (uppercase = bitwise operations)
        # These must come before boolean operators because "AND".lower() == "and"
        # AND gate (bitwise)
        if op in {"AND", "band"} or op_lower in {"&", "ampersand"}:
            res = self.builder.and_(left, right, name="and_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res
        # OR gate (bitwise)
        if op in {"OR", "bor"} or op_lower in {"|", "pipe"}:
            res = self.builder.or_(left, right, name="or_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res
        # XOR gate (bitwise, exclusive or)
        if op in {"XOR", "bxor"}:
            res = self.builder.xor(left, right, name="xor_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res
        # NAND gate (universal gate)
        if op in {"NAND", "nand"}:
            and_result = self.builder.and_(left, right, name="nand_and")
            res = self.builder.not_(and_result, name="nand_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res
        # NOR gate (universal gate)
        if op in {"NOR", "nor"}:
            or_result = self.builder.or_(left, right, name="nor_or")
            res = self.builder.not_(or_result, name="nor_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res
        # XNOR gate (equality gate)
        if op in {"XNOR", "xnor"}:
            xor_result = self.builder.xor(left, right, name="xnor_xor")
            res = self.builder.not_(xor_result, name="xnor_gate")
            self.codegen.set_signedness(res, not use_unsigned)
            return res

        # Power operator (** or ^). Integer exponentiation must stay in the
        # declared integer width; routing it through f64/i64 silently destroys
        # i128..i8192 semantics and loses precision even below overflow.
        if op_lower in {"**", "^", "power"}:
            if not is_float:
                return self._safe_integer_pow(left, right, is_unsigned=use_unsigned)
            pow_func = self._get_pow_intrinsic()
            left_f = (
                left
                if self._is_float_type(left.type)
                else self.builder.sitofp(left, ir.DoubleType(), name="pow_base")
            )
            right_f = (
                right
                if self._is_float_type(right.type)
                else self.builder.sitofp(right, ir.DoubleType(), name="pow_exp")
            )
            return self.builder.call(pow_func, [left_f, right_f], name="pow_call")

        cmp_map = {
            "==": "==",
            "eq": "==",
            "!=": "!=",
            "ne": "!=",
            "<": "<",
            "lt": "<",
            "<=": "<=",
            "le": "<=",
            ">": ">",
            "gt": ">",
            ">=": ">=",
            "ge": ">=",
        }
        if op_lower in cmp_map:
            predicate = cmp_map[op_lower]
            if is_float:
                # IEEE 754: NaN != NaN should be TRUE, NaN == NaN should be FALSE
                # Use unordered comparison for != (returns TRUE if either is NaN)
                # Use ordered comparison for == (returns FALSE if either is NaN)
                if predicate == "!=":
                    return self.builder.fcmp_unordered(
                        predicate, left, right, name="fcmp_une"
                    )
                return self.builder.fcmp_ordered(predicate, left, right, name="fcmp")
            if use_unsigned:
                return self.builder.icmp_unsigned(predicate, left, right, name="icmpu")
            return self.builder.icmp_signed(predicate, left, right, name="icmps")

        # Shift operators with bounds checking
        if op_lower in {"shl", "<<", "lshift"}:
            if shift_amount_literal_in_range(node.right, left.type.width):
                res = self.builder.shl(left, right, name="shl_proven")
            else:
                res = self._safe_shift(left, right, is_left=True)
            self.codegen.set_signedness(res, not left_unsigned)
            return res
        if op_lower in {"shr", ">>", "rshift"}:
            # Signed values shift arithmetically; unsigned values shift logically.
            if shift_amount_literal_in_range(node.right, left.type.width):
                res = (
                    self.builder.lshr(left, right, name="shr_u_proven")
                    if left_unsigned
                    else self.builder.ashr(left, right, name="shr_proven")
                )
            else:
                res = self._safe_shift(
                    left, right, is_left=False, is_logical=left_unsigned
                )
            self.codegen.set_signedness(res, not left_unsigned)
            return res
        if op_lower == "ushr":
            # Logical shift right (zero-fill)
            if shift_amount_literal_in_range(node.right, left.type.width):
                res = self.builder.lshr(left, right, name="ushr_proven")
            else:
                res = self._safe_shift(left, right, is_left=False, is_logical=True)
            self.codegen.set_signedness(res, False)
            return res

        raise ExprGenError(f"Unknown binary operator: {node.op}")

    def _safe_integer_pow(
        self: Any, left: ir.Value, right: ir.Value, *, is_unsigned: bool
    ) -> ir.Value:
        """Exponentiation-by-squaring in the operand's real integer width.

        Negative integer exponents are rejected. Multiplications reuse the
        normal checked arithmetic path, so overflow semantics are identical to
        ``*`` for i8 through i8192. No f64/i64 round-trip is permitted.
        """
        if not isinstance(left.type, ir.IntType) or not isinstance(
            right.type, ir.IntType
        ):
            raise ExprGenError("Integer power requires integer operands")
        if left.type != right.type:
            raise ExprGenError("Integer power operands must have matching widths")

        int_type = left.type
        zero = ir.Constant(int_type, 0)
        one = ir.Constant(int_type, 1)

        if not is_unsigned:
            is_negative = self.builder.icmp_signed("<", right, zero, name="pow_exp_neg")
            error_block = self.function.append_basic_block("pow_negative_exp")
            start_block = self.function.append_basic_block("pow_start")
            self.builder.cbranch(is_negative, error_block, start_block)

            self.builder.position_at_end(error_block)
            error_msg = self.codegen.create_string_constant(
                "Error: Negative integer exponent is not supported!\n"
            )
            self.builder.call(self.codegen.get_printf(), [error_msg])
            self.codegen._emit_safety_trap("Negative integer exponent")
            self.builder.position_at_end(start_block)

        result_ptr = self.codegen.alloca_in_entry_block(int_type, "pow_result")
        base_ptr = self.codegen.alloca_in_entry_block(int_type, "pow_base_i")
        exp_ptr = self.codegen.alloca_in_entry_block(int_type, "pow_exp_i")
        self.builder.store(one, result_ptr)
        self.builder.store(left, base_ptr)
        self.builder.store(right, exp_ptr)

        cond_block = self.function.append_basic_block("pow_cond")
        body_block = self.function.append_basic_block("pow_body")
        done_block = self.function.append_basic_block("pow_done")
        self.builder.branch(cond_block)

        self.builder.position_at_end(cond_block)
        exp_value = self.builder.load(exp_ptr, name="pow_exp_val")
        has_bits = self.builder.icmp_unsigned("!=", exp_value, zero, name="pow_more")
        self.builder.cbranch(has_bits, body_block, done_block)

        self.builder.position_at_end(body_block)
        exp_value = self.builder.load(exp_ptr, name="pow_exp_cur")
        low_bit = self.builder.and_(exp_value, one, name="pow_low_bit")
        odd = self.builder.icmp_unsigned("!=", low_bit, zero, name="pow_odd")
        mul_result_block = self.function.append_basic_block("pow_mul_result")
        after_result_block = self.function.append_basic_block("pow_after_result")
        self.builder.cbranch(odd, mul_result_block, after_result_block)

        self.builder.position_at_end(mul_result_block)
        result_value = self.builder.load(result_ptr, name="pow_result_val")
        base_value = self.builder.load(base_ptr, name="pow_base_val")
        product = self.codegen.safe_mul(
            result_value, base_value, is_unsigned=is_unsigned
        )
        self.builder.store(product, result_ptr)
        self.builder.branch(after_result_block)

        self.builder.position_at_end(after_result_block)
        exp_value = self.builder.load(exp_ptr, name="pow_exp_shift_src")
        next_exp = self.builder.lshr(exp_value, one, name="pow_exp_next")
        self.builder.store(next_exp, exp_ptr)
        need_square = self.builder.icmp_unsigned(
            "!=", next_exp, zero, name="pow_need_square"
        )
        square_block = self.function.append_basic_block("pow_square")
        loop_block = self.function.append_basic_block("pow_loop")
        self.builder.cbranch(need_square, square_block, loop_block)

        self.builder.position_at_end(square_block)
        base_value = self.builder.load(base_ptr, name="pow_base_square_src")
        squared = self.codegen.safe_mul(base_value, base_value, is_unsigned=is_unsigned)
        self.builder.store(squared, base_ptr)
        self.builder.branch(loop_block)

        self.builder.position_at_end(loop_block)
        self.builder.branch(cond_block)

        self.builder.position_at_end(done_block)
        result = self.builder.load(result_ptr, name="pow_value")
        self.codegen.set_signedness(result, not is_unsigned)
        return result

    def _safe_shift(
        self: Any,
        left: ir.Value,
        right: ir.Value,
        is_left: bool,
        is_logical: bool = False,
    ) -> ir.Value:
        """Generate safe shift with bounds checking.

        Shift amount must be in range [0, bit_width). Negative or excessive
        shift amounts cause undefined behavior in C/LLVM.
        """
        bit_width = left.type.width
        max_shift = ir.Constant(right.type, bit_width)
        zero = ir.Constant(right.type, 0)

        # Check: 0 <= shift_amount < bit_width
        is_negative = self.builder.icmp_signed("<", right, zero)
        is_too_large = self.builder.icmp_signed(">=", right, max_shift)
        is_invalid = self.builder.or_(is_negative, is_too_large)

        error_block = self.function.append_basic_block("shift_error")
        ok_block = self.function.append_basic_block("shift_ok")
        self.builder.cbranch(is_invalid, error_block, ok_block)

        # Error block
        self.builder.position_at_end(error_block)
        error_msg = self.codegen.create_string_constant(
            f"Error: Shift amount out of bounds [0, {bit_width})!\n"
        )
        printf = self.codegen.get_printf()
        self.builder.call(printf, [error_msg])
        self.codegen._emit_safety_trap(f"Shift amount out of bounds [0, {bit_width})")

        # OK block - perform the shift
        self.builder.position_at_end(ok_block)
        if is_left:
            return self.builder.shl(left, right, name="shl")
        if is_logical:
            return self.builder.lshr(left, right, name="ushr")
        return self.builder.ashr(left, right, name="shr")

    def visit_TernaryOp(self: Any, node: TernaryOp):
        cond_val = self.codegen.to_bool(self.generate_expr(node.cond))
        then_block = self.function.append_basic_block("ternary_then")
        else_block = self.function.append_basic_block("ternary_else")
        merge_block = self.function.append_basic_block("ternary_merge")

        self.builder.cbranch(cond_val, then_block, else_block)

        self.builder.position_at_end(then_block)
        true_val = self.generate_expr(node.true_expr)
        self.builder.branch(merge_block)
        then_end = self.builder.block

        self.builder.position_at_end(else_block)
        false_val = self.generate_expr(node.false_expr)
        self.builder.branch(merge_block)
        else_end = self.builder.block

        if true_val.type != false_val.type:
            raise TypeError("Type mismatch in ternary expression branches")

        self.builder.position_at_end(merge_block)
        phi = self.builder.phi(true_val.type, name="ternary")
        phi.add_incoming(true_val, then_end)
        phi.add_incoming(false_val, else_end)
        return phi
