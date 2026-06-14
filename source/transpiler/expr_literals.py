"""Literal-expression visitors for LLVM expression generation.

Extracted from ``emit_expressions.py`` as part of the LLVM-side
ExprGenerator decomposition. Method bodies are unchanged.
"""

from __future__ import annotations

from parser.ast import (
    ArrayLit,
    Bool,
    InterpolatedString,
    ListComprehension,
    Null,
    Number,
    Range,
    StringLit,
)
from typing import Any

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ExprGenError


class ExprLiteralEmitter:
    """Literal-expression service for ``ExprGenerator``.

    Holds a back-reference to the parent emitter and forwards unknown
    attribute access so legacy method bodies continue to use ``self``
    exactly as before.
    """

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def visit_Number(self, node: Number) -> ir.Constant:
        if node.is_float:
            precision = (node.precision or "d").lower()
            value = float(node.value)
            if precision == "f":
                const = ir.Constant(ir.FloatType(), value)
                self.codegen.set_signedness(const, True)
                return const
            # Quad precision currently lowered to double until full support lands.
            const = ir.Constant(ir.DoubleType(), value)
            self.codegen.set_signedness(const, True)
            return const
        val_int = int(node.value)
        # Choose width based on magnitude; support up to 8192 bits
        bitlen = val_int.bit_length() or 1
        candidate_widths = [64, 128, 256, 512, 1024, 2048, 4096, 8192]
        width = candidate_widths[-1]
        for w in candidate_widths:
            if bitlen <= w:
                width = w
                break
        # If explicit long flag was set, ensure at least 128 bits
        if node.is_long and width < 128:
            width = 128
        const = ir.Constant(ir.IntType(width), val_int)
        self.codegen.set_signedness(const, True)
        return const

    def visit_Bool(self, node: Bool) -> ir.Constant:
        return ir.Constant(ir.IntType(1), 1 if node.value else 0)

    def visit_Null(self, _node: Null) -> ir.Constant:
        """Generate null pointer (i8* nullptr)."""
        return ir.Constant(ir.IntType(8).as_pointer(), None)

    def visit_ReinterpretCast(self, node) -> ir.Value:
        """Reinterpret/bitcast: reinterpret(target_type, expr)."""
        value = self.generate_expr(node.value)
        target_llvm = self._type_name_to_llvm(node.target_type)
        src_type = value.type
        # Pointer to pointer
        if isinstance(src_type, ir.PointerType) and isinstance(
            target_llvm, ir.PointerType
        ):
            return self.builder.bitcast(value, target_llvm, name="reinterp")
        # Int to pointer
        if isinstance(src_type, ir.IntType) and isinstance(target_llvm, ir.PointerType):
            return self.builder.inttoptr(value, target_llvm, name="reinterp")
        # Pointer to int
        if isinstance(src_type, ir.PointerType) and isinstance(target_llvm, ir.IntType):
            return self.builder.ptrtoint(value, target_llvm, name="reinterp")
        # Int to int (truncate or extend)
        if isinstance(src_type, ir.IntType) and isinstance(target_llvm, ir.IntType):
            if src_type.width > target_llvm.width:
                return self.builder.trunc(value, target_llvm, name="reinterp")
            if src_type.width < target_llvm.width:
                return self.builder.zext(value, target_llvm, name="reinterp")
            return value
        # Float to int (bitcast)
        if isinstance(src_type, (ir.FloatType, ir.DoubleType)) and isinstance(
            target_llvm, ir.IntType
        ):
            return self.builder.bitcast(value, target_llvm, name="reinterp")
        # Int to float (bitcast)
        if isinstance(src_type, ir.IntType) and isinstance(
            target_llvm, (ir.FloatType, ir.DoubleType)
        ):
            return self.builder.bitcast(value, target_llvm, name="reinterp")
        return self.builder.bitcast(value, target_llvm, name="reinterp")

    def visit_StringLit(self, node: StringLit):
        return self.codegen.create_string_constant(node.value)

    def visit_InterpolatedString(self, node: InterpolatedString):
        """Generate code for interpolated string: "Hello #{name}!"

        Strategy: Build the string by concatenating parts using sprintf.
        For each expression, convert to string and concatenate.
        """
        if not node.parts:
            return self.codegen.create_string_constant("")

        # If only one part and it's a string, just return it
        if len(node.parts) == 1 and isinstance(node.parts[ARG_FIRST], str):
            return self.codegen.create_string_constant(node.parts[ARG_FIRST])

        # Build format string and collect values for sprintf
        format_parts = []
        values = []

        for part in node.parts:
            if isinstance(part, str):
                # Literal text - escape % for printf
                format_parts.append(part.replace("%", "%%"))
            else:
                # Expression - generate code and determine format specifier
                value = self.generate_expr(part)
                val_type = value.type

                if isinstance(val_type, ir.IntType):
                    if val_type.width == 1:
                        # Boolean - convert to "true"/"false"
                        format_parts.append("%s")
                        # Create conditional string
                        true_str = self.codegen.create_string_constant("true")
                        false_str = self.codegen.create_string_constant("false")
                        str_val = self.builder.select(value, true_str, false_str)
                        values.append(str_val)
                    else:
                        format_parts.append("%lld")
                        # Extend to i64 for printf compatibility
                        if val_type.width < 64:
                            value = self.builder.sext(value, ir.IntType(64))
                        values.append(value)
                elif isinstance(val_type, (ir.FloatType, ir.DoubleType)):
                    format_parts.append("%g")
                    # Convert float to double for printf
                    if isinstance(val_type, ir.FloatType):
                        value = self.builder.fpext(value, ir.DoubleType())
                    values.append(value)
                elif isinstance(val_type, ir.PointerType):
                    # Assume it's a string pointer
                    format_parts.append("%s")
                    values.append(value)
                else:
                    # Unknown type, try as integer
                    format_parts.append("%lld")
                    values.append(value)

        # Create format string
        format_str = "".join(format_parts)
        format_ptr = self.codegen.create_string_constant(format_str)

        # Allocate buffer for result (generous size, bounded by snprintf)
        buffer_size = 1024
        i8 = ir.IntType(8)
        buffer_type = ir.ArrayType(i8, buffer_size)
        buffer = self.builder.alloca(buffer_type, name="interp_buf")
        buffer_ptr = self.builder.bitcast(buffer, i8.as_pointer())

        # Call snprintf (safe — truncates at buffer_size)
        snprintf_func = self.codegen.get_snprintf()
        size_val = ir.Constant(ir.IntType(64), buffer_size)
        args = [buffer_ptr, size_val, format_ptr, *values]
        self.builder.call(snprintf_func, args)

        return buffer_ptr

    def visit_ArrayLit(self, node: ArrayLit):
        if not node.elements:
            elem_type = ir.IntType(8)
            null_ptr = ir.Constant(elem_type.as_pointer(), None)
            return (null_ptr, 0, elem_type)

        element_values = [self.generate_expr(element) for element in node.elements]
        elem_type = element_values[ARG_FIRST].type
        array_len = len(element_values)

        # Use dynamic array format (with len/cap header) so that
        # array_push / array_pop / array_len work on literal arrays.
        # Layout: [len: i64][cap: i64][data: elem_type * cap]
        int64 = ir.IntType(64)
        initial_cap = max(array_len * 2, 4)
        elem_byte_size = 8  # i64 or pointer — both 8 bytes on 64-bit
        header_bytes = 16  # 2 x i64
        total_bytes = header_bytes + initial_cap * elem_byte_size

        raw_ptr = self.builder.call(
            self.codegen.get_malloc(),
            [ir.Constant(int64, total_bytes)],
            name="arr_lit_alloc",
        )
        i64_ptr = self.builder.bitcast(raw_ptr, int64.as_pointer(), name="arr_lit_hdr")
        # Write header: len, cap
        self.builder.store(ir.Constant(int64, array_len), i64_ptr)
        cap_slot = self.builder.gep(
            i64_ptr, [ir.Constant(ir.IntType(32), 1)], name="arr_lit_cap"
        )
        self.builder.store(ir.Constant(int64, initial_cap), cap_slot)
        # Data starts at offset 2 (in i64 units = byte offset 16)
        data_i64 = self.builder.gep(
            i64_ptr, [ir.Constant(ir.IntType(32), 2)], name="arr_lit_data"
        )
        # Bitcast data pointer to match the actual element type
        data_ptr = self.builder.bitcast(
            data_i64, elem_type.as_pointer(), name="arr_lit_typed"
        )

        for index, element_value in enumerate(element_values):
            if element_value.type != elem_type:
                raise TypeError(
                    f"Inconsistent array literal types: expected "
                    f"{elem_type}, got {element_value.type}"
                )
            dest = self.builder.gep(
                data_ptr,
                [ir.Constant(ir.IntType(32), index)],
                name=f"arr_lit_elem_{index}",
            )
            self.builder.store(element_value, dest)

        return (data_ptr, array_len, elem_type)

    def visit_ListComprehension(self, node: ListComprehension):
        """Generate list comprehension: [expr for var in range]

        For now, only supports Range iterables with known bounds.
        Creates a fixed-size array and fills it with computed values.
        """
        if not isinstance(node.iterable, Range):
            raise ExprGenError("List comprehension currently only supports ranges")

        range_node = node.iterable
        int64 = ir.IntType(64)

        # Get range bounds (must be compile-time constants for now)
        start_val = self.generate_expr(range_node.start)
        end_val = self.generate_expr(range_node.end)

        # For simplicity, compute max possible size (assume small ranges)
        # In a real implementation, we'd need dynamic allocation
        max_size = 1000  # Fixed upper bound
        elem_type = int64  # Default to i64

        array_type = ir.ArrayType(elem_type, max_size)
        array_ptr = self.builder.alloca(array_type, name="listcomp")
        count_ptr = self.builder.alloca(int64, name="listcomp_count")
        self.builder.store(ir.Constant(int64, 0), count_ptr)

        # Loop variable
        loop_var_ptr = self.builder.alloca(int64, name=node.var_name)
        self.builder.store(start_val, loop_var_ptr)

        # Save previous binding
        prev_binding = self.codegen.locals.get(node.var_name)
        self.codegen.locals[node.var_name] = loop_var_ptr

        # Create loop blocks
        current_func = self.builder.function
        cond_block = current_func.append_basic_block("lc_cond")
        body_block = current_func.append_basic_block("lc_body")
        step_block = current_func.append_basic_block("lc_step")
        exit_block = current_func.append_basic_block("lc_exit")

        self.builder.branch(cond_block)

        # Condition
        self.builder.position_at_end(cond_block)
        current_val = self.builder.load(loop_var_ptr)
        if range_node.inclusive:
            cond = self.builder.icmp_signed("<=", current_val, end_val)
        else:
            cond = self.builder.icmp_signed("<", current_val, end_val)
        self.builder.cbranch(cond, body_block, exit_block)

        # Body
        self.builder.position_at_end(body_block)

        # Check optional condition
        if node.condition:
            cond_val = self.generate_expr(node.condition)
            add_block = current_func.append_basic_block("lc_add")
            skip_block = current_func.append_basic_block("lc_skip")
            self.builder.cbranch(cond_val, add_block, skip_block)

            self.builder.position_at_end(add_block)
            # Generate expression and store
            expr_val = self.generate_expr(node.expr)
            count = self.builder.load(count_ptr)
            count_i32 = self.builder.trunc(count, ir.IntType(32))
            elem_ptr = self.builder.gep(
                array_ptr,
                [ir.Constant(ir.IntType(32), 0), count_i32],
                name="lc_elem",
            )
            self.builder.store(expr_val, elem_ptr)
            new_count = self.builder.add(count, ir.Constant(int64, 1))
            self.builder.store(new_count, count_ptr)
            self.builder.branch(step_block)

            self.builder.position_at_end(skip_block)
            self.builder.branch(step_block)
        else:
            # No condition - always add
            expr_val = self.generate_expr(node.expr)
            count = self.builder.load(count_ptr)
            count_i32 = self.builder.trunc(count, ir.IntType(32))
            elem_ptr = self.builder.gep(
                array_ptr,
                [ir.Constant(ir.IntType(32), 0), count_i32],
                name="lc_elem",
            )
            self.builder.store(expr_val, elem_ptr)
            new_count = self.builder.add(count, ir.Constant(int64, 1))
            self.builder.store(new_count, count_ptr)
            self.builder.branch(step_block)

        # Step
        self.builder.position_at_end(step_block)
        current_val = self.builder.load(loop_var_ptr)
        next_val = self.builder.add(current_val, ir.Constant(int64, 1))
        self.builder.store(next_val, loop_var_ptr)
        self.builder.branch(cond_block)

        # Exit
        self.builder.position_at_end(exit_block)

        # Restore binding
        if prev_binding is not None:
            self.codegen.locals[node.var_name] = prev_binding
        else:
            self.codegen.locals.pop(node.var_name, None)

        # Return array with max_size (static allocation)
        # Actual count is dynamic but we use max_size for bounds checking
        return (array_ptr, max_size, elem_type)
