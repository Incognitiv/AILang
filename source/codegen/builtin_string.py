"""Scalar string builtins for LLVM codegen."""

from __future__ import annotations

from parser.ast import (
    ASTNode,
)
from typing import Any

from codegen.codegen import CodeGenError
from codegen.strlen_fact_cache import lookup_strlen_fact
from codegen.strlen_scalarization import try_emit_baseconv_strlen
from llvmlite import ir
from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.expr_string_fastpath import literal_char_at_byte_value

from .builtin_string_conversion_mixin import BuiltinStringConversionMixin


class BuiltinStringEmitter(BuiltinStringConversionMixin):
    """String builtin emitters used by the LLVM expression pipeline."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def _emit_unchecked_char_load(
        self, s_ptr: ir.Value, idx64: ir.Value, name: str
    ) -> ir.Value:
        ptr = self.current_builder.gep(s_ptr, [idx64], name=f"{name}_ptr")
        ch = self.current_builder.load(ptr, name=f"{name}_val")
        return self.current_builder.zext(ch, ir.IntType(64), name=f"{name}_i64")

    def builtin_char_at(
        self, args: list[ASTNode], call_node: ASTNode | None = None
    ) -> ir.Value:
        """Get character at index with bounds checking.

        char_at(str, idx) - safe, calls strlen each time (slow)
        char_at(str, idx, len) - fast, uses provided length for bounds check
        """
        length_arg: ASTNode | None = None
        if len(args) == 2:
            string_arg, index_arg = args
        elif len(args) == 3:
            string_arg, index_arg, length_arg = args
        else:
            raise CodeGenError(
                "char_at() expects (string, index) or (string, index, length)"
            )

        literal_ch = literal_char_at_byte_value(string_arg, index_arg)
        if literal_ch is not None:
            return ir.Constant(ir.IntType(64), literal_ch)

        s_ptr = self.generate_expr(string_arg)
        idx_val = self.generate_expr(index_arg)
        idx64 = self.ensure_int64(idx_val)

        if length_arg is not None:
            index_value = int_literal_value(index_arg)
            length_value = int_literal_value(length_arg)
            if (
                index_value is not None
                and length_value is not None
                and 0 <= index_value < length_value
            ):
                return self._emit_unchecked_char_load(
                    s_ptr, idx64, "char_at_len_proven"
                )

        facts = getattr(self._cg, "range_facts", None)
        func_scope = getattr(self._cg, "_current_function_name", None)
        if (
            call_node is not None
            and facts is not None
            and hasattr(facts, "is_safe_char_at_call")
            and facts.is_safe_char_at_call(func_scope, call_node)
        ):
            return self._emit_unchecked_char_load(s_ptr, idx64, "char_at_proven")

        if len(args) == 2:
            strlen = lookup_strlen_fact(self._cg, string_arg)
            if strlen is None:
                strlen = self.current_builder.call(
                    self.get_strlen(), [s_ptr], name="char_at_len"
                )
        else:
            if length_arg is None:
                raise CodeGenError("char_at() internal length argument missing")
            strlen = self.ensure_int64(self.generate_expr(length_arg))

        zero = ir.Constant(ir.IntType(64), 0)
        in_range = self.current_builder.and_(
            self.current_builder.icmp_signed(">=", idx64, zero),
            self.current_builder.icmp_signed("<", idx64, strlen),
        )

        then_block = self.current_function.append_basic_block("char_at_ok")
        else_block = self.current_function.append_basic_block("char_at_oob")
        self.current_builder.cbranch(in_range, then_block, else_block)

        self.current_builder.position_at_end(else_block)
        self._emit_string_bounds_error(idx64, strlen)

        self.current_builder.position_at_end(then_block)
        return self._emit_unchecked_char_load(s_ptr, idx64, "char")

    def builtin_unsafe_char_at(self, args: list[ASTNode]) -> ir.Value:
        """Get character at index without bounds checking."""
        if len(args) != 2:
            raise CodeGenError("unsafe_char_at() expects (string, index)")
        string_arg, index_arg = args
        s_ptr = self.generate_expr(string_arg)
        idx_val = self.generate_expr(index_arg)
        idx64 = self.ensure_int64(idx_val)

        return self._emit_unchecked_char_load(s_ptr, idx64, "unsafe_char")

    def builtin_index_of(self, args: list[ASTNode]) -> ir.Value:
        """Find first occurrence of substring, return index or -1."""
        if len(args) == 2:
            haystack_arg, needle_arg = args
            hay = self.generate_expr(haystack_arg)
            needle = self.generate_expr(needle_arg)
            found_ptr = self.current_builder.call(
                self.get_strstr(), [hay, needle], name="idx_strstr"
            )
            null_ptr = ir.Constant(found_ptr.type, None)
            is_null = self.current_builder.icmp_unsigned("==", found_ptr, null_ptr)

            base_int = self.current_builder.ptrtoint(
                hay, ir.IntType(64), name="idx_base"
            )
            found_int = self.current_builder.ptrtoint(
                found_ptr, ir.IntType(64), name="idx_found"
            )
            diff = self.current_builder.sub(found_int, base_int, name="idx_diff")
            idx = self.current_builder.trunc(diff, ir.IntType(64), name="idx64")

            minus_one = ir.Constant(ir.IntType(64), -1)
            return self.current_builder.select(is_null, minus_one, idx, name="index_of")
        if len(args) == 3:
            return self.builtin_index_of_from(args)
        raise CodeGenError(
            "index_of() expects (haystack, needle) or (haystack, needle, start)"
        )

    def builtin_index_of_from(self, args: list[ASTNode]) -> ir.Value:
        """Find first occurrence of substring from byte offset, return index or -1."""
        if len(args) != 3:
            raise CodeGenError("index_of_from() expects (haystack, needle, start)")
        haystack_arg, needle_arg, start_arg = args
        hay = self.generate_expr(haystack_arg)
        needle = self.generate_expr(needle_arg)
        start = self.ensure_int64(self.generate_expr(start_arg))

        int64 = ir.IntType(64)
        zero64 = ir.Constant(int64, 0)
        minus_one = ir.Constant(int64, -1)

        direct_block = self.current_function.append_basic_block("idxf_direct")
        positive_block = self.current_function.append_basic_block("idxf_positive")
        search_block = self.current_function.append_basic_block("idxf_search")
        past_end_block = self.current_function.append_basic_block("idxf_past_end")
        merge_block = self.current_function.append_basic_block("idxf_merge")

        start_le_zero = self.current_builder.icmp_signed(
            "<=", start, zero64, name="idxf_start_le_zero"
        )
        self.current_builder.cbranch(start_le_zero, direct_block, positive_block)

        self.current_builder.position_at_end(direct_block)
        direct_ptr = self.current_builder.call(
            self.get_strstr(), [hay, needle], name="idxf_direct_strstr"
        )
        direct_val = self._index_result_from_ptr(hay, direct_ptr, "idxf_direct")
        self.current_builder.branch(merge_block)
        direct_end = self.current_builder.block

        self.current_builder.position_at_end(positive_block)
        strlen = self.current_builder.call(self.get_strlen(), [hay], name="idxf_len")
        start_in_range = self.current_builder.icmp_signed(
            "<=", start, strlen, name="idxf_start_in_range"
        )
        self.current_builder.cbranch(start_in_range, search_block, past_end_block)

        self.current_builder.position_at_end(search_block)
        search_ptr = self.current_builder.gep(hay, [start], name="idxf_search_ptr")
        found_ptr = self.current_builder.call(
            self.get_strstr(), [search_ptr, needle], name="idxf_strstr"
        )
        found_val = self._index_result_from_ptr(hay, found_ptr, "idxf_found")
        self.current_builder.branch(merge_block)
        search_end = self.current_builder.block

        self.current_builder.position_at_end(past_end_block)
        self.current_builder.branch(merge_block)
        past_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        result = self.current_builder.phi(int64, name="index_of_from")
        result.add_incoming(direct_val, direct_end)
        result.add_incoming(found_val, search_end)
        result.add_incoming(minus_one, past_end)
        return result

    def _index_result_from_ptr(
        self, hay: ir.Value, found_ptr: ir.Value, name: str
    ) -> ir.Value:
        int64 = ir.IntType(64)
        minus_one = ir.Constant(int64, -1)
        null_ptr = ir.Constant(found_ptr.type, None)
        is_null = self.current_builder.icmp_unsigned(
            "==", found_ptr, null_ptr, name=f"{name}_is_null"
        )
        base_int = self.current_builder.ptrtoint(hay, int64, name=f"{name}_base")
        found_int = self.current_builder.ptrtoint(found_ptr, int64, name=f"{name}_ptr")
        diff = self.current_builder.sub(found_int, base_int, name=f"{name}_diff")
        return self.current_builder.select(is_null, minus_one, diff, name=f"{name}_idx")

    def builtin_substr(self, args: list[ASTNode]) -> ir.Value:
        """Extract substring from string with bounds checking."""
        if len(args) != 3:
            raise CodeGenError("substr() expects (string, start, length)")
        string_arg, start_arg, length_arg = args
        s_ptr = self.generate_expr(string_arg)
        start_val = self.ensure_int64(self.generate_expr(start_arg))
        len_val = self.ensure_int64(self.generate_expr(length_arg))

        zero64 = ir.Constant(ir.IntType(64), 0)
        strlen = self.current_builder.call(
            self.get_strlen(), [s_ptr], name="substr_len"
        )

        start_ge0 = self.current_builder.icmp_signed(">=", start_val, zero64)
        start_ok_block = self.current_function.append_basic_block("substr_start_ok")
        start_bad_block = self.current_function.append_basic_block("substr_start_bad")
        merge_block = self.current_function.append_basic_block("substr_merge")
        self.current_builder.cbranch(start_ge0, start_ok_block, start_bad_block)

        self.current_builder.position_at_end(start_bad_block)
        empty = self.create_string_constant("")
        self.current_builder.branch(merge_block)
        bad_end = self.current_builder.block

        self.current_builder.position_at_end(start_ok_block)
        start_le_len = self.current_builder.icmp_signed("<", start_val, strlen)
        substr_start = self.current_builder.select(
            start_le_len, start_val, strlen, name="substr_start"
        )

        max_len = self.current_builder.sub(strlen, substr_start, name="substr_max")
        len_nonneg = self.current_builder.select(
            self.current_builder.icmp_signed(">=", len_val, zero64), len_val, zero64
        )
        use_len = self.current_builder.select(
            self.current_builder.icmp_signed("<=", len_nonneg, max_len),
            len_nonneg,
            max_len,
        )

        total = self.current_builder.add(use_len, ir.Constant(ir.IntType(64), 1))
        dest = self.string_alloc(total, "substr_buf")
        src_ptr = self.current_builder.gep(
            s_ptr,
            [self.current_builder.trunc(substr_start, ir.IntType(32))],
            name="substr_src",
        )
        self.current_builder.call(self.get_memcpy(), [dest, src_ptr, use_len])
        end_ptr = self.current_builder.gep(
            dest,
            [self.current_builder.trunc(use_len, ir.IntType(32))],
            name="substr_end",
        )
        self.current_builder.store(ir.Constant(ir.IntType(8), 0), end_ptr)
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        phi = self.current_builder.phi(ir.IntType(8).as_pointer(), name="substr_result")
        phi.add_incoming(empty, bad_end)
        phi.add_incoming(dest, ok_end)
        return phi

    def builtin_concat(self, args: list[ASTNode]) -> ir.Value:
        """Concatenate multiple strings."""
        if len(args) < 2:
            raise CodeGenError("concat() expects at least 2 arguments")

        int64 = ir.IntType(64)
        zero64 = ir.Constant(int64, 0)
        one64 = ir.Constant(int64, 1)

        str_vals = [self.generate_expr(arg) for arg in args]

        total_len = zero64
        for s in str_vals:
            slen = self.current_builder.call(self.get_strlen(), [s])
            total_len = self.current_builder.add(total_len, slen, name="concat_accum")

        alloc_size = self.current_builder.add(total_len, one64, name="concat_alloc")
        result = self.string_alloc(alloc_size, "concat_buf")

        current_pos = result
        for i, s in enumerate(str_vals):
            slen = self.current_builder.call(self.get_strlen(), [s])
            self.current_builder.call(self.get_memcpy(), [current_pos, s, slen])
            current_pos = self.current_builder.gep(
                current_pos,
                [self.current_builder.trunc(slen, ir.IntType(32))],
                name=f"concat_pos{i}",
            )

        self.current_builder.store(ir.Constant(ir.IntType(8), 0), current_pos)
        return result

    def builtin_ord(self, args: list[ASTNode]) -> ir.Value:
        """Get ASCII code of first character."""
        if len(args) != 1:
            raise CodeGenError("ord() expects exactly 1 argument")
        (string_arg,) = args
        s_ptr = self.generate_expr(string_arg)

        if isinstance(s_ptr.type, ir.IntType):
            return self.ensure_int64(s_ptr)

        strlen_val = self.current_builder.call(
            self.get_strlen(), [s_ptr], name="ord_len"
        )
        zero = ir.Constant(ir.IntType(64), 0)
        is_empty = self.current_builder.icmp_signed("==", strlen_val, zero)

        ok_block = self.current_function.append_basic_block("ord_ok")
        empty_block = self.current_function.append_basic_block("ord_empty")
        merge_block = self.current_function.append_basic_block("ord_merge")

        self.current_builder.cbranch(is_empty, empty_block, ok_block)

        self.current_builder.position_at_end(empty_block)
        neg_one = ir.Constant(ir.IntType(64), -1)
        self.current_builder.branch(merge_block)
        empty_end = self.current_builder.block

        self.current_builder.position_at_end(ok_block)
        first_char = self.current_builder.load(s_ptr, name="first_char")
        char_i64 = self.current_builder.zext(first_char, ir.IntType(64), name="ord_val")
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        phi = self.current_builder.phi(ir.IntType(64), name="ord_result")
        phi.add_incoming(neg_one, empty_end)
        phi.add_incoming(char_i64, ok_end)
        return phi

    def builtin_chr(self, args: list[ASTNode]) -> ir.Value:
        """Convert ASCII code to single-character string."""
        if len(args) != 1:
            raise CodeGenError("chr() expects exactly 1 argument")
        (code_arg,) = args
        code_val = self.ensure_int64(self.generate_expr(code_arg))

        zero = ir.Constant(ir.IntType(64), 0)
        max_code = ir.Constant(ir.IntType(64), 255)
        ge_zero = self.current_builder.icmp_signed(">=", code_val, zero)
        le_max = self.current_builder.icmp_signed("<=", code_val, max_code)
        is_valid = self.current_builder.and_(ge_zero, le_max)

        ok_block = self.current_function.append_basic_block("chr_ok")
        bad_block = self.current_function.append_basic_block("chr_bad")
        merge_block = self.current_function.append_basic_block("chr_merge")

        self.current_builder.cbranch(is_valid, ok_block, bad_block)

        self.current_builder.position_at_end(bad_block)
        empty_str = self.create_string_constant("")
        self.current_builder.branch(merge_block)
        bad_end = self.current_builder.block

        self.current_builder.position_at_end(ok_block)
        two = ir.Constant(ir.IntType(64), 2)
        buf = self.string_alloc(two, "chr_buf")
        char_val = self.current_builder.trunc(code_val, ir.IntType(8), name="chr_val")
        self.current_builder.store(char_val, buf)
        one_i32 = ir.Constant(ir.IntType(32), 1)
        end_ptr = self.current_builder.gep(buf, [one_i32], name="chr_end")
        self.current_builder.store(ir.Constant(ir.IntType(8), 0), end_ptr)
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        phi = self.current_builder.phi(ir.IntType(8).as_pointer(), name="chr_result")
        phi.add_incoming(empty_str, bad_end)
        phi.add_incoming(buf, ok_end)
        return phi

    def builtin_strlen(self, args: list[ASTNode]) -> ir.Value:
        """Get length of string."""
        if len(args) != 1:
            raise CodeGenError("strlen() expects exactly 1 argument")
        (string_arg,) = args
        cached_len = self._try_emit_cached_strlen(string_arg)
        if cached_len is not None:
            return cached_len
        base_len = try_emit_baseconv_strlen(self._cg, string_arg)
        if base_len is not None:
            return base_len
        virtual_len = self._try_emit_virtual_strlen(string_arg)
        if virtual_len is not None:
            return virtual_len
        s_ptr = self.generate_expr(string_arg)
        return self.current_builder.call(self.get_strlen(), [s_ptr], name="strlen_val")
