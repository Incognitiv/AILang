"""Identifier/operator visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

import sys
from parser.ast import BinaryOp, Call, StringLit, UnaryOp, Variable
from typing import Any

from ast_access import arg_at
from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ExprGenError
from transpiler.llvm_bigint import (
    bigint_from_decimal_literal,
    clone_if_borrowed,
    fixed_to_bigint,
    free_if_owned_temp,
    is_bigint_value,
    is_unbounded_spec,
)

from .expr_ops_binary_mixin import ExprOpsBinaryMixin


class ExprOpsEmitter(ExprOpsBinaryMixin):
    """Identifier and operator-expression service for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def visit_Variable(self, node: Variable):
        constants = getattr(self.codegen, "local_constant_values", None)
        if isinstance(constants, dict):
            constant = constants.get(node.name)
            if isinstance(constant, ir.Constant) and isinstance(
                constant.type, ir.IntType
            ):
                self.codegen.set_signedness(
                    constant, self.codegen.var_signedness.get(node.name, True)
                )
                return constant

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

        if is_bigint_value(self.codegen, operand):
            if op.lower() in {"+", "plus"}:
                return clone_if_borrowed(
                    self.codegen, self.builder, node.operand, operand
                )
            if op.lower() in {"-", "minus"}:
                zero = self.builder.call(
                    self.codegen._get_bigint_from_int(),
                    [ir.Constant(ir.IntType(64), 0)],
                    name="bigint_neg_zero",
                )
                result = self.builder.call(
                    self.codegen._get_bigint_sub(), [zero, operand], name="bigint_neg"
                )
                self.builder.call(self.codegen._get_bigint_free(), [zero])
                free_if_owned_temp(self.codegen, self.builder, node.operand, operand)
                return result
            if op in {"NOT", "bnot", "~", "tilde"}:
                result = self.builder.call(
                    self.codegen._get_bigint_not(), [operand], name="bigint_not"
                )
                free_if_owned_temp(self.codegen, self.builder, node.operand, operand)
                return result
            if op.lower() in {"not", "!"}:
                result = self.builder.xor(
                    self.codegen.to_bool(operand),
                    ir.Constant(ir.IntType(1), 1),
                    name="not",
                )
                free_if_owned_temp(self.codegen, self.builder, node.operand, operand)
                return result
            raise ExprGenError(f"Unknown unary operator for unbounded: {node.op}")

        if op.lower() in {"+", "plus"}:
            return operand
        if op.lower() in {"-", "minus"}:
            if self._is_float_type(operand.type):
                zero = ir.Constant(operand.type, 0.0)
                return self.builder.fsub(zero, operand, name="fneg")
            if isinstance(operand.type, ir.IntType):
                zero = ir.Constant(operand.type, 0)
                return self.builder.sub(zero, operand, name="neg")
        if op in {"NOT", "bnot", "~", "tilde"}:
            if isinstance(operand.type, ir.IntType):
                return self.builder.not_(operand, name="not_gate")
            raise ExprGenError("Bitwise NOT requires integer operand")
        if op.lower() in {"not", "!"}:
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
        if isinstance(value.type, ir.IntType) and value.type.width > 64:
            tail_text = self.codegen.wide_int_to_decimal_string(
                value, self.codegen.is_unsigned_value(value)
            )
            tail_len = self.builder.call(
                self.codegen.get_strlen(), [tail_text], name="str_lit_wide_len"
            )
            total = self.builder.add(
                ir.Constant(int64, prefix_len + 1), tail_len, name="str_lit_wide_total"
            )
            result = self.codegen.string_alloc(total, "str_lit_wide_buf")
            if prefix_len:
                self.builder.call(
                    self.codegen.get_memcpy(),
                    [result, prefix_ptr, ir.Constant(int64, prefix_len)],
                )
            tail = self.builder.gep(
                result, [ir.Constant(int64, prefix_len)], name="str_lit_wide_tail"
            )
            copy_len = self.builder.add(tail_len, ir.Constant(int64, 1))
            self.builder.call(self.codegen.get_memcpy(), [tail, tail_text, copy_len])
            return result

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

    def _expr_is_unbounded(self, node: Any) -> bool:
        if isinstance(node, Variable):
            spec = getattr(self.codegen, "local_decl_types", {}).get(node.name)
            if spec is None:
                spec = getattr(self.codegen, "global_decl_types", {}).get(node.name)
            return spec is not None and is_unbounded_spec(self.codegen, spec)
        if isinstance(node, Call):
            fn = getattr(self.codegen, "_function_nodes", {}).get(node.name)
            spec = getattr(fn, "return_type", None)
            return spec is not None and is_unbounded_spec(self.codegen, spec)
        if isinstance(node, BinaryOp):
            return self._expr_is_unbounded(node.left) or self._expr_is_unbounded(
                node.right
            )
        if isinstance(node, UnaryOp):
            return self._expr_is_unbounded(node.operand)
        return False

    def _bigint_operand(self, node: Any) -> tuple[ir.Value, bool]:
        """Return (value, owned-temporary) for an arbitrary-precision operand."""
        if isinstance(node, __import__("parser.ast", fromlist=["Number"]).Number):
            value = bigint_from_decimal_literal(self.codegen, self.builder, node)
            return value, True
        value = self.generate_expr(node)
        if is_bigint_value(self.codegen, value):
            from transpiler.llvm_bigint import expression_is_borrowed_bigint

            return value, not expression_is_borrowed_bigint(node)
        if isinstance(value.type, ir.IntType):
            value = fixed_to_bigint(
                self.codegen,
                self.builder,
                value,
                unsigned=self.codegen.is_unsigned_value(value),
            )
            return value, True
        raise ExprGenError(
            f"unbounded arithmetic requires integer operand, got {value.type}"
        )

    def _bigint_nonnegative_i64(self, node: Any) -> tuple[ir.Value, ir.Value | None]:
        """Convert exponent/shift count to non-negative i64 without truncation."""
        owned_bigint = None
        if self._expr_is_unbounded(node):
            value, owned = self._bigint_operand(node)
            fits = self.builder.call(
                self.codegen._get_bigint_fits_signed(),
                [value, ir.Constant(ir.IntType(64), 64)],
                name="bigint_count_fits",
            )
            sign = self.builder.call(
                self.codegen._get_bigint_sign(), [value], name="bigint_count_sign"
            )
            fit_ok = self.builder.icmp_unsigned(
                "!=", fits, ir.Constant(ir.IntType(64), 0)
            )
            nonneg = self.builder.icmp_signed(
                ">=", sign, ir.Constant(ir.IntType(64), 0)
            )
            ok_cond = self.builder.and_(fit_ok, nonneg)
            fail = self.function.append_basic_block("bigint_count_fail")
            ok = self.function.append_basic_block("bigint_count_ok")
            self.builder.cbranch(ok_cond, ok, fail)
            self.builder.position_at_end(fail)
            msg = self.codegen.create_string_constant(
                "Error: unbounded shift/exponent must fit non-negative i64!\n"
            )
            self.builder.call(self.codegen.get_printf(), [msg])
            self.codegen._emit_safety_trap("invalid unbounded shift/exponent")
            self.builder.position_at_end(ok)
            out = self.builder.call(
                self.codegen._get_bigint_to_i64(), [value], name="bigint_count_i64"
            )
            owned_bigint = value if owned else None
            return out, owned_bigint
        raw = self.generate_expr(node)
        out = self.ensure_int64(raw)
        neg = self.builder.icmp_signed(
            "<", out, ir.Constant(ir.IntType(64), 0), name="bigint_count_negative"
        )
        fail = self.function.append_basic_block("bigint_count_negative_fail")
        ok = self.function.append_basic_block("bigint_count_nonnegative")
        self.builder.cbranch(neg, fail, ok)
        self.builder.position_at_end(fail)
        msg = self.codegen.create_string_constant(
            "Error: shift/exponent cannot be negative!\n"
        )
        self.builder.call(self.codegen.get_printf(), [msg])
        self.codegen._emit_safety_trap("negative shift/exponent")
        self.builder.position_at_end(ok)
        return out, None
