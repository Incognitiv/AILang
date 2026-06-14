"""Identifier/operator visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

import sys
from parser.ast import BinaryOp, Call, StringLit, TernaryOp, UnaryOp, Variable
from typing import Any

from ast_access import arg_at
from llvmlite import ir
from transpiler.arithmetic_literal_proofs import (
    literal_int_arithmetic_safe,
    neutral_int_arithmetic_safe,
    positive_int_literal,
    shift_amount_literal_in_range,
)
from transpiler.expr_common import ARG_FIRST, ExprGenError


class ExprOpsEmitter:
    """Identifier and operator-expression service for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def visit_Variable(self, node: Variable):
        if node.name in self.codegen.locals:
            stored_value = self.codegen.locals[node.name]

            # Check if this is an alloca instruction (needs load) or SSA value (use directly)
            # AllocaInstr check: either via opname or type name
            is_alloca = (
                hasattr(stored_value, "opname") and stored_value.opname == "alloca"
            ) or type(stored_value).__name__ == "AllocaInstr"

            if is_alloca:
                # This is a stack slot - load the value
                loaded = self.builder.load(stored_value, name=f"{node.name}_val")
                self.codegen.set_signedness(
                    loaded, self.codegen.var_signedness.get(node.name, True)
                )
                return loaded
            # This is an SSA value (e.g., function parameter) - use directly
            self.codegen.set_signedness(
                stored_value, self.codegen.var_signedness.get(node.name, True)
            )
            return stored_value

        # Check for global variables
        if node.name in self.codegen.globals:
            global_var = self.codegen.globals[node.name]
            # Check if this is a global array
            if node.name in self.codegen.array_metadata:
                array_len, elem_type = self.codegen.array_metadata[node.name]
                # Return pointer to first element plus metadata
                return (global_var, array_len, elem_type)
            # Load the value from the global variable
            loaded = self.builder.load(global_var, name=f"{node.name}_global")
            return loaded

        # Enum member without qualification (e.g. `Color.RED` or `RED`).
        if any(key.startswith(f"{node.name}.") for key in self.codegen.enum_values):
            raise ExprGenError(
                f"Cannot treat enum '{node.name}' as value. Use 'EnumName.{node.name}'."
            )

        matching_members = [
            value
            for name, value in self.codegen.enum_values.items()
            if name.endswith(f".{node.name}")
        ]
        if len(matching_members) == 1:
            return ir.Constant(ir.IntType(64), matching_members[ARG_FIRST])
        if matching_members:
            raise ExprGenError(
                f"Ambiguous enum member '{node.name}'. Qualify it with the enum name."
            )

        raise ExprGenError(f"Undefined variable: {node.name}")

    def visit_UnaryOp(self, node: UnaryOp):
        operand = self.generate_expr(node.operand)
        op = node.op

        if op.lower() in {"+", "plus"}:
            # Unary plus is a no-op, just return the operand
            return operand
        if op.lower() in {"-", "minus"}:
            if self._is_float_type(operand.type):
                zero = ir.Constant(operand.type, 0.0)
                return self.builder.fsub(zero, operand, name="fneg")
            if isinstance(operand.type, ir.IntType):
                zero = ir.Constant(operand.type, 0)
                return self.builder.sub(zero, operand, name="neg")
        # Check uppercase NOT first (bitwise gate)
        if op in {"NOT", "bnot", "~", "tilde"}:
            # Bitwise NOT gate (flip all bits)
            if isinstance(operand.type, ir.IntType):
                return self.builder.not_(operand, name="not_gate")
            raise ExprGenError("Bitwise NOT requires integer operand")
        # Then check lowercase not (boolean)
        if op.lower() in {"not", "!"}:
            # Logical NOT (boolean)
            return self.builder.xor(
                self.codegen.to_bool(operand), ir.Constant(ir.IntType(1), 1), name="not"
            )

        raise ExprGenError(f"Unknown unary operator: {node.op}")

    def _check_constant_overflow(
        self, left: ir.Value, right: ir.Value, op: str, result_width: int
    ) -> None:
        """Check for compile-time overflow on constant expressions.

        Integer Safety 10/10: Detect overflow at compile time for constant
        expressions like '255 + 1' assigned to a byte.
        """
        if not (isinstance(left, ir.Constant) and isinstance(right, ir.Constant)):
            return

        try:
            left_val = left.constant
            right_val = right.constant
        except (AttributeError, TypeError):
            return

        # Calculate the result
        if op in {"+", "plus"}:
            result = left_val + right_val
        elif op in {"-", "minus"}:
            result = left_val - right_val
        elif op in {"*", "star"}:
            result = left_val * right_val
        else:
            return

        # Check if result fits in the result width
        max_val = (1 << result_width) - 1
        min_val = -(1 << (result_width - 1))

        if result > max_val or result < min_val:

            print(
                f"Warning: Compile-time overflow detected: {left_val} {op} {right_val} "
                f"= {result} (exceeds {result_width}-bit range [{min_val}, {max_val}])",
                file=sys.stderr,
            )

    def _try_emit_literal_str_concat(self, node: BinaryOp) -> ir.Value | None:
        """Fuse "literal" + str(number) into one LLVM string allocation.

        The generic path emits str(number) into a temporary and then emits a
        second allocation plus strlen/strcpy/strcat for concatenation.  This
        pattern is common in packet/object naming hot loops, so lower it
        directly to: copy literal prefix, sprintf number at the tail.
        """
        if not isinstance(node.left, StringLit):
            return None
        if not isinstance(node.right, Call):
            return None
        if node.right.name != "str" or len(node.right.args) != 1:
            return None

        value = self.generate_expr(arg_at(node.right, 0))
        prefix = node.left.value
        prefix_len = len(prefix.encode("utf-8"))

        int64 = ir.IntType(64)
        prefix_ptr = self.codegen.create_string_constant(prefix)
        numeric_tail_size = (
            64 if isinstance(value.type, (ir.FloatType, ir.DoubleType)) else 32
        )
        buf_size = ir.Constant(int64, prefix_len + numeric_tail_size)
        result = self.codegen.string_alloc(buf_size, "str_lit_i64_buf")

        if prefix_len:
            self.builder.call(
                self.codegen.get_memcpy(),
                [result, prefix_ptr, ir.Constant(int64, prefix_len)],
            )

        tail = self.builder.gep(
            result, [ir.Constant(int64, prefix_len)], name="str_lit_i64_tail"
        )
        if isinstance(value.type, (ir.FloatType, ir.DoubleType)):
            sprintf_fn = self.codegen.get_sprintf()
            if isinstance(value.type, ir.FloatType):
                value = self.builder.fpext(value, ir.DoubleType(), name="f2d")
            fmt = self.codegen.create_string_constant("%g")
            self.builder.call(sprintf_fn, [tail, fmt, value], name="sprintf_lit_f")
        else:
            value = self.ensure_int64(value)
            self.builder.call(
                self.codegen.get_i64_to_cstr_func(),
                [tail, value],
                name="write_lit_i64",
            )
        return result

    def visit_BinaryOp(self, node: BinaryOp):
        op = node.op  # Keep original case for gate operators
        op_lower = op.lower()

        if op_lower in {"+", "plus"}:
            fused = self._try_emit_literal_str_concat(node)
            if fused is not None:
                return fused

        left = self.generate_expr(node.left)
        right = self.generate_expr(node.right)

        use_unsigned = self._is_unsigned_node(node.left) or self._is_unsigned_node(
            node.right
        )

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

        left, right, is_float = self._coerce_numeric_operands(left, right, use_unsigned)

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
            res = self.codegen.safe_division(
                left, right, is_float=is_float, is_unsigned=use_unsigned
            )
            if not is_float:
                self.codegen.set_signedness(res, not use_unsigned)
            return res
        if op_lower in {"%", "mod"}:
            if not is_float and positive_int_literal(node.right):
                if use_unsigned:
                    res = self.builder.urem(left, right, name="urem_proven")
                    self.codegen.set_signedness(res, False)
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
            return self.builder.and_(left, right, name="and_gate")
        # OR gate (bitwise)
        if op in {"OR", "bor"} or op_lower in {"|", "pipe"}:
            return self.builder.or_(left, right, name="or_gate")
        # XOR gate (bitwise, exclusive or)
        if op in {"XOR", "bxor"}:
            return self.builder.xor(left, right, name="xor_gate")
        # NAND gate (universal gate)
        if op in {"NAND", "nand"}:
            and_result = self.builder.and_(left, right, name="nand_and")
            return self.builder.not_(and_result, name="nand_gate")
        # NOR gate (universal gate)
        if op in {"NOR", "nor"}:
            or_result = self.builder.or_(left, right, name="nor_or")
            return self.builder.not_(or_result, name="nor_gate")
        # XNOR gate (equality gate)
        if op in {"XNOR", "xnor"}:
            xor_result = self.builder.xor(left, right, name="xnor_xor")
            return self.builder.not_(xor_result, name="xnor_gate")

        # Boolean operators (lowercase = logical true/false)
        # Short-circuit: right operand only evaluated when needed
        if op_lower in {"and", "&&"}:
            left_bool = self.codegen.to_bool(left)
            and_true_block = self.codegen.current_function.append_basic_block("and_rhs")
            and_merge_block = self.codegen.current_function.append_basic_block(
                "and_merge"
            )
            entry_block = self.builder.block
            self.builder.cbranch(left_bool, and_true_block, and_merge_block)

            # Evaluate right only if left is true
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
        if op_lower in {"or", "||"}:
            left_bool = self.codegen.to_bool(left)
            or_false_block = self.codegen.current_function.append_basic_block("or_rhs")
            or_merge_block = self.codegen.current_function.append_basic_block(
                "or_merge"
            )
            entry_block = self.builder.block
            self.builder.cbranch(left_bool, or_merge_block, or_false_block)

            # Evaluate right only if left is false
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
        # Power operator (** or ^) - math exponentiation
        if op_lower in {"**", "^", "power"}:
            pow_func = self._get_pow_intrinsic()
            left_f = (
                self.builder.sitofp(left, ir.DoubleType(), name="pow_base")
                if not is_float
                else left
            )
            right_f = (
                self.builder.sitofp(right, ir.DoubleType(), name="pow_exp")
                if not is_float
                else right
            )
            result_f = self.builder.call(pow_func, [left_f, right_f], name="pow_call")
            if is_float:
                return result_f
            # Use safe conversion to avoid UB on large powers
            return self._safe_fptosi(result_f, ir.IntType(64))

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
                return self.builder.shl(left, right, name="shl_proven")
            return self._safe_shift(left, right, is_left=True)
        if op_lower in {"shr", ">>", "rshift"}:
            # Arithmetic shift right (preserves sign)
            if shift_amount_literal_in_range(node.right, left.type.width):
                return self.builder.ashr(left, right, name="shr_proven")
            return self._safe_shift(left, right, is_left=False, is_logical=False)
        if op_lower == "ushr":
            # Logical shift right (zero-fill)
            if shift_amount_literal_in_range(node.right, left.type.width):
                return self.builder.lshr(left, right, name="ushr_proven")
            return self._safe_shift(left, right, is_left=False, is_logical=True)

        raise ExprGenError(f"Unknown binary operator: {node.op}")

    def _safe_shift(
        self, left: ir.Value, right: ir.Value, is_left: bool, is_logical: bool = False
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

    def visit_TernaryOp(self, node: TernaryOp):
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
