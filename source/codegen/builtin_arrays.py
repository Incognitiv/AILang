"""Builtin array helpers extracted from ``codegen.CodeGen``."""

from __future__ import annotations

from parser.ast import ASTNode, FieldAccess, Number, ThisExpr, Variable
from typing import Any

from codegen.codegen import CodeGenError
from llvmlite import ir


class BuiltinArrayEmitter:
    """Emit LLVM IR for dynamic array-related builtins."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    # --------------------------------------------------------------------
    # Dynamic array (i64) built-ins: array_new, array_len, array_cap, array_push, array_pop
    # Layout: data_ptr points to element 0; data_ptr[-2] = len (i64), data_ptr[-1] = cap (i64)
    # Caller must keep returned pointer (push may realloc)
    # --------------------------------------------------------------------

    def _array_header_ptr(self, data_ptr: ir.Value) -> ir.Value:
        # data_ptr is i64*, header is two i64 slots before it
        return self.current_builder.gep(
            data_ptr, [ir.Constant(ir.IntType(32), -2)], name="arr_hdr"
        )

    def _stack_array_scalar_values(self, array_arg: ASTNode) -> tuple[Any, ...] | None:
        if not isinstance(array_arg, FieldAccess):
            return None
        owner: str | None = None
        if isinstance(array_arg.object_expr, Variable):
            owner = array_arg.object_expr.name
        elif isinstance(array_arg.object_expr, ThisExpr):
            owner = getattr(self._cg, "_inline_this_stack_var", None)
        if owner is None:
            return None
        return getattr(self._cg, "_stack_array_field_values", {}).get(
            (owner, array_arg.field_name)
        )

    def builtin_array_len(self, args: list[ASTNode]) -> ir.Value:
        """Get current length of dynamic array."""
        if len(args) != 1:
            raise CodeGenError("array_len() expects (array)")
        (array_arg,) = args
        if isinstance(array_arg, Variable) and array_arg.name in self.array_metadata:
            array_len, _elem_type = self.array_metadata[array_arg.name]
            return ir.Constant(ir.IntType(64), array_len)
        scalar_values = self._stack_array_scalar_values(array_arg)
        if scalar_values is not None:
            return ir.Constant(ir.IntType(64), len(scalar_values))
        data_ptr = self.generate_expr(array_arg)
        hdr = self._array_header_ptr(data_ptr)
        return self.current_builder.load(hdr, name="arr_len")

    def builtin_array_cap(self, args: list[ASTNode]) -> ir.Value:
        """Get current capacity of dynamic array."""
        if len(args) != 1:
            raise CodeGenError("array_cap() expects (array)")
        (array_arg,) = args
        data_ptr = self.generate_expr(array_arg)
        hdr = self._array_header_ptr(data_ptr)
        cap_ptr = self.current_builder.gep(
            hdr, [ir.Constant(ir.IntType(32), 1)], name="arr_cap_ptr"
        )
        return self.current_builder.load(cap_ptr, name="arr_cap")

    def builtin_array_new(self, args: list[ASTNode]) -> ir.Value:
        """Create new dynamic array with specified capacity."""
        if len(args) != 1:
            raise CodeGenError("array_new() expects (capacity)")
        (capacity_arg,) = args
        cap_val = self.ensure_int64(self.generate_expr(capacity_arg))
        zero = ir.Constant(ir.IntType(64), 0)
        cap_pos = self.current_builder.select(
            self.current_builder.icmp_signed(">", cap_val, zero),
            cap_val,
            zero,
            name="cap_pos",
        )
        # bytes = (cap * 8) + 16 (len+cap headers)
        elem_size = ir.Constant(ir.IntType(64), 8)
        header_size = ir.Constant(ir.IntType(64), 16)
        bytes_needed = self.current_builder.add(
            self.current_builder.mul(cap_pos, elem_size, name="arr_bytes_data"),
            header_size,
            name="arr_bytes",
        )
        raw_ptr = self.current_builder.call(
            self.get_malloc(), [bytes_needed], name="arr_alloc"
        )
        data_ptr = self.current_builder.bitcast(
            raw_ptr, ir.IntType(64).as_pointer(), name="arr_i64ptr"
        )
        # Set len=0, cap=cap_pos
        hdr = data_ptr
        self.current_builder.store(zero, hdr)
        cap_ptr = self.current_builder.gep(
            hdr, [ir.Constant(ir.IntType(32), 1)], name="arr_cap_ptr"
        )
        self.current_builder.store(cap_pos, cap_ptr)
        data_start = self.current_builder.gep(
            data_ptr, [ir.Constant(ir.IntType(32), 2)], name="arr_data"
        )
        return data_start

    def builtin_array_push(self, args: list[ASTNode]) -> ir.Value:
        """Append value to dynamic array, growing if needed."""
        if len(args) != 2:
            raise CodeGenError("array_push() expects (array, value)")
        array_arg, value_arg = args
        data_ptr = self.generate_expr(array_arg)

        # Ensure data_ptr is i64* for header access
        if str(data_ptr.type) != "i64*":
            data_ptr = self.current_builder.bitcast(
                data_ptr, ir.IntType(64).as_pointer(), name="arr_i64cast"
            )

        value = self.ensure_int64(self.generate_expr(value_arg))
        hdr = self._array_header_ptr(data_ptr)
        cap_ptr = self.current_builder.gep(
            hdr, [ir.Constant(ir.IntType(32), 1)], name="arr_cap_ptr"
        )
        len_val = self.current_builder.load(hdr, name="arr_len")
        cap_val = self.current_builder.load(cap_ptr, name="arr_cap")

        need_grow = self.current_builder.icmp_unsigned(">=", len_val, cap_val)
        grow_block = self.current_function.append_basic_block("arr_grow")
        ok_block = self.current_function.append_basic_block("arr_ok")
        merge_block = self.current_function.append_basic_block("arr_push_merge")
        self.current_builder.cbranch(need_grow, grow_block, ok_block)

        # Grow path
        self.current_builder.position_at_end(grow_block)
        one = ir.Constant(ir.IntType(64), 1)
        new_cap = self.current_builder.select(
            self.current_builder.icmp_unsigned(
                "==", cap_val, ir.Constant(ir.IntType(64), 0)
            ),
            one,
            self.current_builder.mul(
                cap_val, ir.Constant(ir.IntType(64), 2), name="arr_cap2"
            ),
        )
        elem_size = ir.Constant(ir.IntType(64), 8)
        header_size = ir.Constant(ir.IntType(64), 16)
        bytes_needed = self.current_builder.add(
            self.current_builder.mul(new_cap, elem_size), header_size, name="arr_bytes2"
        )
        # raw base pointer = data_ptr - 2
        raw_base = self.current_builder.gep(
            data_ptr, [ir.Constant(ir.IntType(32), -2)], name="arr_raw_base"
        )
        raw_base_i8 = self.current_builder.bitcast(raw_base, ir.IntType(8).as_pointer())
        new_raw = self.current_builder.call(
            self.get_realloc(), [raw_base_i8, bytes_needed], name="arr_realloc"
        )
        new_i64 = self.current_builder.bitcast(
            new_raw, ir.IntType(64).as_pointer(), name="arr_realloc_i64"
        )
        new_hdr = new_i64
        new_cap_ptr = self.current_builder.gep(
            new_hdr, [ir.Constant(ir.IntType(32), 1)], name="arr_new_cap_ptr"
        )
        self.current_builder.store(new_cap, new_cap_ptr)
        new_data = self.current_builder.gep(
            new_i64, [ir.Constant(ir.IntType(32), 2)], name="arr_new_data"
        )
        self.current_builder.branch(merge_block)
        grow_end = self.current_builder.block

        # OK path
        self.current_builder.position_at_end(ok_block)
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        # Merge
        self.current_builder.position_at_end(merge_block)
        data_phi = self.current_builder.phi(
            ir.IntType(64).as_pointer(), name="arr_data_phi"
        )
        hdr_phi = self.current_builder.phi(
            ir.IntType(64).as_pointer(), name="arr_hdr_phi"
        )
        data_phi.add_incoming(data_ptr, ok_end)
        hdr_phi.add_incoming(hdr, ok_end)
        data_phi.add_incoming(new_data, grow_end)
        hdr_phi.add_incoming(new_hdr, grow_end)

        # Write value and bump len
        len_cur = self.current_builder.load(hdr_phi, name="arr_len_cur")
        # L11 fix: Use i64 index to support arrays >2B elements
        dest_ptr = self.current_builder.gep(data_phi, [len_cur], name="arr_dest")
        self.current_builder.store(value, dest_ptr)
        len_next = self.current_builder.add(len_cur, ir.Constant(ir.IntType(64), 1))
        self.current_builder.store(len_next, hdr_phi)
        return data_phi

    def builtin_array_pop(self, args: list[ASTNode]) -> ir.Value:
        """Remove and return last element from dynamic array."""
        if len(args) != 1:
            raise CodeGenError("array_pop() expects (array)")
        (array_arg,) = args
        data_ptr = self.generate_expr(array_arg)
        hdr = self._array_header_ptr(data_ptr)
        len_val = self.current_builder.load(hdr, name="arr_len")
        zero = ir.Constant(ir.IntType(64), 0)
        is_empty = self.current_builder.icmp_unsigned("==", len_val, zero)

        empty_block = self.current_function.append_basic_block("arr_pop_empty")
        ok_block = self.current_function.append_basic_block("arr_pop_ok")
        merge_block = self.current_function.append_basic_block("arr_pop_merge")
        self.current_builder.cbranch(is_empty, empty_block, ok_block)

        self.current_builder.position_at_end(ok_block)
        len_prev = self.current_builder.sub(
            len_val, ir.Constant(ir.IntType(64), 1), name="arr_len_prev"
        )
        self.current_builder.store(len_prev, hdr)
        dest_ptr = self.current_builder.gep(
            data_ptr,
            [self.current_builder.trunc(len_prev, ir.IntType(32))],
            name="arr_pop_ptr",
        )
        val = self.current_builder.load(dest_ptr, name="arr_pop_val")
        self.current_builder.branch(merge_block)

        self.current_builder.position_at_end(empty_block)
        # Ada-style constraint error: fail safely instead of silently returning 0
        error_msg = self.create_string_constant(
            "Error: array_pop() called on empty array!\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("array_pop() called on empty array")

        # Merge block only reached from ok_block (empty block is unreachable)
        self.current_builder.position_at_end(merge_block)
        return val

    def builtin_array_get(self, args: list[ASTNode]) -> ir.Value:
        """Get element at index from dynamic int array: array_get(arr, idx) -> int."""
        if len(args) != 2:
            raise CodeGenError("array_get() expects (array, index)")
        array_arg, index_arg = args
        scalar_values = self._stack_array_scalar_values(array_arg)
        if (
            scalar_values is not None
            and isinstance(index_arg, Number)
            and not index_arg.is_float
        ):
            index_value = int(index_arg.value)
            if 0 <= index_value < len(scalar_values):
                return scalar_values[index_value]
        data_ptr = self.generate_expr(array_arg)
        index = self.ensure_int64(self.generate_expr(index_arg))
        elem_ptr = self.current_builder.gep(data_ptr, [index], name="arr_get_ptr")
        return self.current_builder.load(elem_ptr, name="arr_get_val")

    def builtin_array_set(self, args: list[ASTNode]) -> ir.Value:
        """Set element at index in dynamic int array: array_set(arr, idx, val)."""
        if len(args) != 3:
            raise CodeGenError("array_set() expects (array, index, value)")
        array_arg, index_arg, value_arg = args
        data_ptr = self.generate_expr(array_arg)
        index = self.ensure_int64(self.generate_expr(index_arg))
        value = self.ensure_int64(self.generate_expr(value_arg))
        elem_ptr = self.current_builder.gep(data_ptr, [index], name="arr_set_ptr")
        self.current_builder.store(value, elem_ptr)
        return data_ptr

    # --------------------------------------------------------------------
    # String/pointer dynamic arrays: str_array_new, str_array_push, etc.
    # Layout mirrors int arrays: data_ptr points to element 0 (i8**),
    # data_ptr[-2] = len (as i8*), data_ptr[-1] = cap (as i8*)
    # Pointer-width slots: each element is an i8* (string pointer).
    # --------------------------------------------------------------------

    def _str_array_header_ptr(self, data_ptr: ir.Value) -> ir.Value:
        """Get header base (i8**) from data pointer. Header is 2 slots before."""
        return self.current_builder.gep(
            data_ptr, [ir.Constant(ir.IntType(32), -2)], name="sarr_hdr"
        )

    def builtin_str_array_new(self, args: list[ASTNode]) -> ir.Value:
        """Create new string dynamic array with specified capacity."""
        if len(args) != 1:
            raise CodeGenError("str_array_new() expects (capacity)")
        (capacity_arg,) = args
        cap_val = self.ensure_int64(self.generate_expr(capacity_arg))
        zero_i64 = ir.Constant(ir.IntType(64), 0)
        cap_pos = self.current_builder.select(
            self.current_builder.icmp_signed(">", cap_val, zero_i64),
            cap_val,
            ir.Constant(ir.IntType(64), 4),
            name="scap_pos",
        )
        # Each slot is pointer-width (8 bytes on 64-bit)
        ptr_size = ir.Constant(ir.IntType(64), 8)
        header_slots = ir.Constant(ir.IntType(64), 2)
        total_slots = self.current_builder.add(cap_pos, header_slots, name="stotal")
        bytes_needed = self.current_builder.mul(total_slots, ptr_size, name="sbytes")
        raw_ptr = self.current_builder.call(
            self.get_malloc(), [bytes_needed], name="sarr_alloc"
        )
        char_pp = ir.IntType(8).as_pointer().as_pointer()
        hdr_ptr = self.current_builder.bitcast(raw_ptr, char_pp, name="sarr_pp")
        # Store len=0 and cap as pointer-sized int via inttoptr
        zero_as_ptr = self.current_builder.inttoptr(
            zero_i64, ir.IntType(8).as_pointer()
        )
        cap_as_ptr = self.current_builder.inttoptr(cap_pos, ir.IntType(8).as_pointer())
        self.current_builder.store(zero_as_ptr, hdr_ptr)
        cap_slot = self.current_builder.gep(
            hdr_ptr, [ir.Constant(ir.IntType(32), 1)], name="scap_slot"
        )
        self.current_builder.store(cap_as_ptr, cap_slot)
        data_start = self.current_builder.gep(
            hdr_ptr, [ir.Constant(ir.IntType(32), 2)], name="sarr_data"
        )
        return data_start

    def builtin_str_array_len(self, args: list[ASTNode]) -> ir.Value:
        """Get length of string dynamic array."""
        if len(args) != 1:
            raise CodeGenError("str_array_len() expects (array)")
        (array_arg,) = args
        data_ptr = self.generate_expr(array_arg)
        hdr = self._str_array_header_ptr(data_ptr)
        raw = self.current_builder.load(hdr, name="sarr_len_raw")
        return self.current_builder.ptrtoint(raw, ir.IntType(64), name="sarr_len")

    def builtin_str_array_push(self, args: list[ASTNode]) -> ir.Value:
        """Push a string onto a string dynamic array. Returns (possibly new) data ptr."""
        if len(args) != 2:
            raise CodeGenError("str_array_push() expects (array, string)")
        array_arg, value_arg = args
        data_ptr = self.generate_expr(array_arg)
        value = self.generate_expr(value_arg)  # i8*
        # Ensure value is i8*
        char_ptr_ty = ir.IntType(8).as_pointer()
        if value.type != char_ptr_ty:
            value = self.current_builder.inttoptr(self.ensure_int64(value), char_ptr_ty)
        hdr = self._str_array_header_ptr(data_ptr)
        cap_slot = self.current_builder.gep(
            hdr, [ir.Constant(ir.IntType(32), 1)], name="scap_slot"
        )
        len_raw = self.current_builder.load(hdr, name="slen_raw")
        cap_raw = self.current_builder.load(cap_slot, name="scap_raw")
        len_val = self.current_builder.ptrtoint(len_raw, ir.IntType(64))
        cap_val = self.current_builder.ptrtoint(cap_raw, ir.IntType(64))

        need_grow = self.current_builder.icmp_unsigned(">=", len_val, cap_val)
        grow_block = self.current_function.append_basic_block("sarr_grow")
        ok_block = self.current_function.append_basic_block("sarr_ok")
        merge_block = self.current_function.append_basic_block("sarr_merge")
        self.current_builder.cbranch(need_grow, grow_block, ok_block)

        # Grow path
        self.current_builder.position_at_end(grow_block)
        new_cap = self.current_builder.mul(
            cap_val, ir.Constant(ir.IntType(64), 2), name="scap2"
        )
        new_cap = self.current_builder.select(
            self.current_builder.icmp_unsigned(
                "==", new_cap, ir.Constant(ir.IntType(64), 0)
            ),
            ir.Constant(ir.IntType(64), 4),
            new_cap,
        )
        ptr_size = ir.Constant(ir.IntType(64), 8)
        total_slots = self.current_builder.add(new_cap, ir.Constant(ir.IntType(64), 2))
        bytes_needed = self.current_builder.mul(total_slots, ptr_size)
        raw_base = self.current_builder.bitcast(
            hdr, ir.IntType(8).as_pointer(), name="sarr_raw_base"
        )
        new_raw = self.current_builder.call(
            self.get_realloc(), [raw_base, bytes_needed], name="sarr_realloc"
        )
        char_pp = ir.IntType(8).as_pointer().as_pointer()
        new_hdr = self.current_builder.bitcast(new_raw, char_pp)
        new_cap_as_ptr = self.current_builder.inttoptr(
            new_cap, ir.IntType(8).as_pointer()
        )
        new_cap_slot = self.current_builder.gep(
            new_hdr, [ir.Constant(ir.IntType(32), 1)]
        )
        self.current_builder.store(new_cap_as_ptr, new_cap_slot)
        new_data = self.current_builder.gep(new_hdr, [ir.Constant(ir.IntType(32), 2)])
        self.current_builder.branch(merge_block)
        grow_end = self.current_builder.block

        # OK path
        self.current_builder.position_at_end(ok_block)
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        # Merge
        self.current_builder.position_at_end(merge_block)
        char_pp = ir.IntType(8).as_pointer().as_pointer()
        data_phi = self.current_builder.phi(char_pp, name="sarr_data_phi")
        hdr_phi = self.current_builder.phi(char_pp, name="sarr_hdr_phi")
        data_phi.add_incoming(data_ptr, ok_end)
        hdr_phi.add_incoming(hdr, ok_end)
        data_phi.add_incoming(new_data, grow_end)
        hdr_phi.add_incoming(new_hdr, grow_end)

        # Write value and bump len
        len_cur_raw = self.current_builder.load(hdr_phi, name="slen_cur")
        len_cur = self.current_builder.ptrtoint(len_cur_raw, ir.IntType(64))
        dest_ptr = self.current_builder.gep(data_phi, [len_cur], name="sarr_dest")
        self.current_builder.store(value, dest_ptr)
        len_next = self.current_builder.add(len_cur, ir.Constant(ir.IntType(64), 1))
        len_next_ptr = self.current_builder.inttoptr(
            len_next, ir.IntType(8).as_pointer()
        )
        self.current_builder.store(len_next_ptr, hdr_phi)
        return data_phi

    def builtin_str_array_get(self, args: list[ASTNode]) -> ir.Value:
        """Get string at index from string dynamic array."""
        if len(args) != 2:
            raise CodeGenError("str_array_get() expects (array, index)")
        array_arg, index_arg = args
        data_ptr = self.generate_expr(array_arg)
        index = self.ensure_int64(self.generate_expr(index_arg))
        # Bounds check
        hdr = self._str_array_header_ptr(data_ptr)
        len_raw = self.current_builder.load(hdr)
        len_val = self.current_builder.ptrtoint(len_raw, ir.IntType(64))
        in_bounds = self.current_builder.icmp_signed("<", index, len_val)

        ok_block = self.current_function.append_basic_block("sarr_get_ok")
        err_block = self.current_function.append_basic_block("sarr_get_oob")
        merge = self.current_function.append_basic_block("sarr_get_merge")

        self.current_builder.cbranch(in_bounds, ok_block, err_block)

        self.current_builder.position_at_end(ok_block)
        elem_ptr = self.current_builder.gep(data_ptr, [index], name="sarr_elem")
        val = self.current_builder.load(elem_ptr, name="sarr_val")
        self.current_builder.branch(merge)
        ok_end = self.current_builder.block

        self.current_builder.position_at_end(err_block)
        empty = self.create_string_constant("")
        self.current_builder.branch(merge)
        err_end = self.current_builder.block

        self.current_builder.position_at_end(merge)
        result = self.current_builder.phi(
            ir.IntType(8).as_pointer(), name="sarr_result"
        )
        result.add_incoming(val, ok_end)
        result.add_incoming(empty, err_end)
        return result

    def builtin_str_array_join(self, args: list[ASTNode]) -> ir.Value:
        """Join str_array elements with separator into a single string.

        Two-pass O(n) implementation: sum total length once, malloc once,
        copy each element + interleaved separator. Mirrors the C version
        in transpiler_c.py:_emit_runtime_str_array (the str_array_join_fn
        function). Replaces the O(n^2) `s = s + ...` loop pattern.
        """
        if len(args) != 2:
            raise CodeGenError("str_array_join() expects (array, separator)")

        array_arg, sep_arg = args
        data_ptr = self.generate_expr(array_arg)  # i8** at slot-2 of header
        sep_val = self.generate_expr(sep_arg)
        char_ptr_ty = ir.IntType(8).as_pointer()
        if sep_val.type != char_ptr_ty:
            sep_val = self.current_builder.inttoptr(
                self.ensure_int64(sep_val), char_ptr_ty
            )

        i64 = ir.IntType(64)
        zero64 = ir.Constant(i64, 0)
        one64 = ir.Constant(i64, 1)

        b = self.current_builder
        func = self.current_function
        strlen_fn = self.get_strlen()
        malloc_fn = self.get_malloc()
        memcpy_fn = self.get_memcpy()

        # Length from header.
        hdr = self._str_array_header_ptr(data_ptr)
        len_raw = b.load(hdr, name="sjoin_len_raw")
        length = b.ptrtoint(len_raw, i64, name="sjoin_len")

        # Empty-array fast path: return "".
        empty_str = self.create_string_constant("")
        is_empty = b.icmp_signed("==", length, zero64, name="sjoin_is_empty")

        empty_block = func.append_basic_block("sjoin_empty")
        nonempty_block = func.append_basic_block("sjoin_nonempty")
        done_block = func.append_basic_block("sjoin_done")
        b.cbranch(is_empty, empty_block, nonempty_block)

        # Empty path -> jump to done with empty_str.
        b.position_at_end(empty_block)
        b.branch(done_block)
        empty_end = b.block

        # Nonempty: compute sep_len (0 if NULL).
        b.position_at_end(nonempty_block)
        sep_is_null = b.icmp_unsigned(
            "==",
            b.ptrtoint(sep_val, i64),
            zero64,
            name="sjoin_sep_null",
        )
        sep_len_call_block = func.append_basic_block("sjoin_sep_len")
        sep_len_done_block = func.append_basic_block("sjoin_sep_done")
        b.cbranch(sep_is_null, sep_len_done_block, sep_len_call_block)

        b.position_at_end(sep_len_call_block)
        sep_len_val = b.call(strlen_fn, [sep_val], name="sjoin_sep_len_val")
        b.branch(sep_len_done_block)
        sep_len_call_end = b.block

        b.position_at_end(sep_len_done_block)
        sep_len = b.phi(i64, name="sjoin_sep_len_phi")
        sep_len.add_incoming(zero64, nonempty_block)
        sep_len.add_incoming(sep_len_val, sep_len_call_end)

        # ----- Pass 1: sum of strlens -----
        sum_loop_hdr = func.append_basic_block("sjoin_sum_hdr")
        sum_loop_body = func.append_basic_block("sjoin_sum_body")
        sum_loop_end = func.append_basic_block("sjoin_sum_end")
        b.branch(sum_loop_hdr)

        b.position_at_end(sum_loop_hdr)
        sum_i = b.phi(i64, name="sjoin_sum_i")
        sum_i.add_incoming(zero64, sep_len_done_block)
        total_acc = b.phi(i64, name="sjoin_total")
        total_acc.add_incoming(zero64, sep_len_done_block)
        cont_sum = b.icmp_signed("<", sum_i, length, name="sjoin_sum_cont")
        b.cbranch(cont_sum, sum_loop_body, sum_loop_end)

        b.position_at_end(sum_loop_body)
        elem_ptr_addr = b.gep(data_ptr, [sum_i], name="sjoin_sum_slot")
        elem_str = b.load(elem_ptr_addr, name="sjoin_sum_str")
        elem_len = b.call(strlen_fn, [elem_str], name="sjoin_sum_strlen")
        next_total = b.add(total_acc, elem_len, name="sjoin_sum_next")
        next_i = b.add(sum_i, one64, name="sjoin_sum_inc")
        sum_i.add_incoming(next_i, sum_loop_body)
        total_acc.add_incoming(next_total, sum_loop_body)
        b.branch(sum_loop_hdr)

        # Add separator overhead: (length - 1) * sep_len.
        b.position_at_end(sum_loop_end)
        seps_count = b.sub(length, one64, name="sjoin_seps")
        seps_total = b.mul(seps_count, sep_len, name="sjoin_seps_total")
        total_with_seps = b.add(total_acc, seps_total, name="sjoin_total_full")
        # Allocate result + 1 NUL byte.
        alloc_size = b.add(total_with_seps, one64, name="sjoin_alloc_sz")
        result_ptr = b.call(malloc_fn, [alloc_size], name="sjoin_buf")

        # ----- Pass 2: copy each element with separator interleave -----
        copy_loop_hdr = func.append_basic_block("sjoin_cp_hdr")
        copy_loop_body = func.append_basic_block("sjoin_cp_body")
        copy_loop_end = func.append_basic_block("sjoin_cp_end")
        b.branch(copy_loop_hdr)

        b.position_at_end(copy_loop_hdr)
        cp_i = b.phi(i64, name="sjoin_cp_i")
        cp_i.add_incoming(zero64, sum_loop_end)
        cp_p = b.phi(char_ptr_ty, name="sjoin_cp_p")
        cp_p.add_incoming(result_ptr, sum_loop_end)
        cont_cp = b.icmp_signed("<", cp_i, length, name="sjoin_cp_cont")
        b.cbranch(cont_cp, copy_loop_body, copy_loop_end)

        b.position_at_end(copy_loop_body)
        # Optional separator before element (when i > 0 and sep_len > 0).
        i_pos = b.icmp_signed(">", cp_i, zero64, name="sjoin_i_pos")
        sep_pos = b.icmp_signed(">", sep_len, zero64, name="sjoin_sep_pos")
        need_sep = b.and_(i_pos, sep_pos, name="sjoin_need_sep")
        sep_blk = func.append_basic_block("sjoin_cp_sep")
        no_sep_blk = func.append_basic_block("sjoin_cp_nosep")
        b.cbranch(need_sep, sep_blk, no_sep_blk)

        b.position_at_end(sep_blk)
        b.call(memcpy_fn, [cp_p, sep_val, sep_len], name="sjoin_cp_sep_call")
        p_after_sep = b.gep(cp_p, [sep_len], name="sjoin_p_after_sep")
        b.branch(no_sep_blk)
        sep_end = b.block

        b.position_at_end(no_sep_blk)
        p_now = b.phi(char_ptr_ty, name="sjoin_p_now")
        p_now.add_incoming(cp_p, copy_loop_body)
        p_now.add_incoming(p_after_sep, sep_end)

        # Copy element.
        cp_elem_addr = b.gep(data_ptr, [cp_i], name="sjoin_cp_slot")
        cp_elem = b.load(cp_elem_addr, name="sjoin_cp_str")
        cp_elem_len = b.call(strlen_fn, [cp_elem], name="sjoin_cp_strlen")
        b.call(memcpy_fn, [p_now, cp_elem, cp_elem_len], name="sjoin_cp_call")
        p_after_elem = b.gep(p_now, [cp_elem_len], name="sjoin_p_after_elem")
        next_cp_i = b.add(cp_i, one64, name="sjoin_cp_inc")
        cp_i.add_incoming(next_cp_i, b.block)
        cp_p.add_incoming(p_after_elem, b.block)
        b.branch(copy_loop_hdr)

        # Terminate the result with NUL.
        b.position_at_end(copy_loop_end)
        nul = ir.Constant(ir.IntType(8), 0)
        end_p = b.gep(result_ptr, [total_with_seps], name="sjoin_end_p")
        b.store(nul, end_p)
        b.branch(done_block)
        nonempty_end = b.block

        # Done: phi between empty_str and result_ptr.
        b.position_at_end(done_block)
        result = b.phi(char_ptr_ty, name="sjoin_result")
        result.add_incoming(empty_str, empty_end)
        result.add_incoming(result_ptr, nonempty_end)
        return result

    def builtin_dealloc_str_array(self, args: list[ASTNode]) -> ir.Value:
        """Free a str_array container without freeing borrowed elements."""
        if len(args) != 1:
            raise CodeGenError("dealloc_str_array() expects (array)")
        (array_arg,) = args
        data_ptr = self.generate_expr(array_arg)
        char_ptr_ty = ir.IntType(8).as_pointer()
        char_pp_ty = char_ptr_ty.as_pointer()
        i64 = ir.IntType(64)
        zero64 = ir.Constant(i64, 0)

        if isinstance(data_ptr.type, ir.IntType):
            data_ptr = self.current_builder.inttoptr(
                self.ensure_int64(data_ptr), char_pp_ty, name="sarr_free_ptr"
            )
        elif data_ptr.type != char_pp_ty:
            data_ptr = self.current_builder.bitcast(
                data_ptr, char_pp_ty, name="sarr_free_ptr"
            )

        raw_value = self.current_builder.ptrtoint(data_ptr, i64, name="sarr_free_raw")
        is_null = self.current_builder.icmp_unsigned(
            "==", raw_value, zero64, name="sarr_free_is_null"
        )
        func = self.current_function
        null_block = func.append_basic_block("sarr_free_null")
        ok_block = func.append_basic_block("sarr_free_ok")
        done_block = func.append_basic_block("sarr_free_done")
        self.current_builder.cbranch(is_null, null_block, ok_block)

        self.current_builder.position_at_end(null_block)
        self.current_builder.branch(done_block)

        self.current_builder.position_at_end(ok_block)
        hdr = self._str_array_header_ptr(data_ptr)
        raw_base = self.current_builder.bitcast(hdr, char_ptr_ty, name="sarr_free_base")
        self.current_builder.call(self._get_free(), [raw_base])
        self.current_builder.branch(done_block)

        self.current_builder.position_at_end(done_block)
        if isinstance(array_arg, Variable):
            local = getattr(self._cg, "locals", {}).get(array_arg.name)
            if local is not None and hasattr(local, "type"):
                pointee = getattr(local.type, "pointee", None)
                if pointee == char_pp_ty:
                    self.current_builder.store(ir.Constant(char_pp_ty, None), local)
        return zero64

    def builtin_str_array_set(self, args: list[ASTNode]) -> ir.Value:
        """Set string at index in string dynamic array.

        Returns the array pointer so `arr = str_array_set(arr, i, s)` does not
        null-overwrite `arr`. Mirrors the array_set fix.
        """
        if len(args) != 3:
            raise CodeGenError("str_array_set() expects (array, index, string)")
        array_arg, index_arg, value_arg = args
        data_ptr = self.generate_expr(array_arg)
        index = self.ensure_int64(self.generate_expr(index_arg))
        value = self.generate_expr(value_arg)
        char_ptr_ty = ir.IntType(8).as_pointer()
        if value.type != char_ptr_ty:
            value = self.current_builder.inttoptr(self.ensure_int64(value), char_ptr_ty)
        elem_ptr = self.current_builder.gep(data_ptr, [index], name="sarr_set")
        self.current_builder.store(value, elem_ptr)
        return data_ptr

    def builtin_str_array_pop(self, args: list[ASTNode]) -> ir.Value:
        """Remove and return last string from string dynamic array."""
        if len(args) != 1:
            raise CodeGenError("str_array_pop() expects (array)")
        (array_arg,) = args
        data_ptr = self.generate_expr(array_arg)
        hdr = self._str_array_header_ptr(data_ptr)
        char_ptr_ty = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)

        # Load length (stored as i8*, need ptrtoint for arithmetic)
        len_raw = self.current_builder.load(hdr, name="sarr_pop_len_raw")
        len_val = self.current_builder.ptrtoint(len_raw, i64, name="sarr_pop_len")
        zero = ir.Constant(i64, 0)
        is_empty = self.current_builder.icmp_unsigned("==", len_val, zero)

        empty_block = self.current_function.append_basic_block("sarr_pop_empty")
        ok_block = self.current_function.append_basic_block("sarr_pop_ok")
        merge_block = self.current_function.append_basic_block("sarr_pop_merge")
        self.current_builder.cbranch(is_empty, empty_block, ok_block)

        self.current_builder.position_at_end(ok_block)
        len_prev = self.current_builder.sub(
            len_val, ir.Constant(i64, 1), name="sarr_pop_prev"
        )
        # Store decremented length back as i8*
        len_prev_ptr = self.current_builder.inttoptr(
            len_prev, char_ptr_ty, name="sarr_pop_prev_ptr"
        )
        self.current_builder.store(len_prev_ptr, hdr)
        # Load the element at the old last index
        elem_ptr = self.current_builder.gep(data_ptr, [len_prev], name="sarr_pop_elem")
        val = self.current_builder.load(elem_ptr, name="sarr_pop_val")
        self.current_builder.branch(merge_block)

        self.current_builder.position_at_end(empty_block)
        error_msg = self.create_string_constant(
            "Error: str_array_pop() called on empty array!\n"
        )
        printf = self.get_printf()
        self.current_builder.call(printf, [error_msg])
        self._emit_safety_trap("str_array_pop() called on empty array")

        self.current_builder.position_at_end(merge_block)
        return val
