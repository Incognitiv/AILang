"""
BuiltinStringEmitter - scalar string builtins for LLVM codegen.

Phase A8 extraction from ``CodeGen``:
- char_at / unsafe_char_at / index_of / substr / concat
- ord / chr / strlen / str
- startswith / endswith / str_replace
"""

from __future__ import annotations

from parser.ast import (
    ASTNode,
    BinaryOp,
    Call,
    FieldAccess,
    Number,
    StringLit,
    ThisExpr,
    UnaryOp,
    Variable,
)
from typing import Any

from codegen.codegen import CodeGenError
from codegen.strlen_fact_cache import lookup_strlen_fact
from llvmlite import ir
from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.expr_string_fastpath import literal_char_at_byte_value


class BuiltinStringEmitter:
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
        if len(args) != 2:
            raise CodeGenError("index_of() expects (haystack, needle)")
        haystack_arg, needle_arg = args
        hay = self.generate_expr(haystack_arg)
        needle = self.generate_expr(needle_arg)
        found_ptr = self.current_builder.call(
            self.get_strstr(), [hay, needle], name="idx_strstr"
        )
        null_ptr = ir.Constant(found_ptr.type, None)
        is_null = self.current_builder.icmp_unsigned("==", found_ptr, null_ptr)

        base_int = self.current_builder.ptrtoint(hay, ir.IntType(64), name="idx_base")
        found_int = self.current_builder.ptrtoint(
            found_ptr, ir.IntType(64), name="idx_found"
        )
        diff = self.current_builder.sub(found_int, base_int, name="idx_diff")
        idx = self.current_builder.trunc(diff, ir.IntType(64), name="idx64")

        minus_one = ir.Constant(ir.IntType(64), -1)
        return self.current_builder.select(is_null, minus_one, idx, name="index_of")

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
        result = self.current_builder.call(
            self.get_malloc(), [alloc_size], name="concat_buf"
        )

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
        virtual_len = self._try_emit_virtual_strlen(string_arg)
        if virtual_len is not None:
            return virtual_len
        s_ptr = self.generate_expr(string_arg)
        return self.current_builder.call(self.get_strlen(), [s_ptr], name="strlen_val")

    def _try_emit_cached_strlen(self, string_arg: ASTNode) -> ir.Value | None:
        if isinstance(string_arg, Variable):
            hidden = f"__ailang_{string_arg.name}_len"
            if hidden in getattr(self, "locals", {}):
                return self.ensure_int64(self.locals[hidden])
            cached = lookup_strlen_fact(self._cg, string_arg)
            if cached is not None:
                return self.ensure_int64(cached)
        if not isinstance(string_arg, FieldAccess):
            return None
        owner_class = None
        if isinstance(string_arg.object_expr, ThisExpr):
            owner_class = getattr(self, "current_class", None)
        elif hasattr(self, "get_variable_class_type") and isinstance(
            string_arg.object_expr, Variable
        ):
            owner_class = self.get_variable_class_type(string_arg.object_expr.name)
        if owner_class is None:
            return None
        try:
            _field_idx, field_type = self.get_field_info(
                owner_class, string_arg.field_name
            )
        except Exception:
            return None
        if str(field_type).strip().lower() not in {"string", "str"}:
            return None
        obj_ptr = self.generate_expr(string_arg.object_expr)
        hidden_name = f"__ailang_{string_arg.field_name}_len"
        try:
            hidden_idx, _ = self.get_field_info(owner_class, hidden_name)
        except Exception:
            return None
        hidden_ptr = self.current_builder.gep(
            obj_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), hidden_idx)],
            name=f"{string_arg.field_name}_len_ptr",
        )
        return self.current_builder.load(
            hidden_ptr, name=f"{string_arg.field_name}_len"
        )

    def _try_emit_virtual_strlen(self, string_arg: ASTNode) -> ir.Value | None:
        """Lower strlen("literal" + str(int)) without materializing the string."""
        if not isinstance(string_arg, BinaryOp):
            return None
        if string_arg.op.lower() not in {"+", "plus"}:
            return None
        if not isinstance(string_arg.left, StringLit):
            return None
        if not isinstance(string_arg.right, Call):
            return None
        if string_arg.right.name != "str" or len(string_arg.right.args) != 1:
            return None

        (value_arg,) = string_arg.right.args
        if not self._str_arg_is_known_integer(value_arg):
            return None

        value = self.generate_expr(value_arg)
        if not isinstance(value.type, ir.IntType):
            return None

        int64 = ir.IntType(64)
        prefix_len = len(string_arg.left.value.encode("utf-8"))
        tail_len = self.current_builder.call(
            self.get_i64_decimal_len_func(),
            [self.ensure_int64(value)],
            name="virtual_i64_strlen",
        )
        if prefix_len == 0:
            return tail_len
        return self.current_builder.add(
            ir.Constant(int64, prefix_len),
            tail_len,
            name="virtual_concat_strlen",
        )

    def _str_arg_is_known_integer(self, node: ASTNode) -> bool:
        if isinstance(node, Number):
            return not node.is_float
        if isinstance(node, Variable):
            local_type = getattr(self, "local_decl_types", {}).get(node.name)
            if local_type is not None:
                return self._is_integer_type_name(local_type)
            local_value = getattr(self, "locals", {}).get(node.name)
            local_llvm_type = getattr(local_value, "type", None)
            if isinstance(local_llvm_type, ir.PointerType):
                local_llvm_type = local_llvm_type.pointee
            return isinstance(local_llvm_type, ir.IntType)
        if isinstance(node, UnaryOp):
            return node.op.lower() in {"+", "plus", "-", "minus"} and (
                self._str_arg_is_known_integer(node.operand)
            )
        if isinstance(node, BinaryOp):
            if node.op.lower() not in {
                "+",
                "plus",
                "-",
                "minus",
                "*",
                "star",
                "%",
                "mod",
                "/",
                "slash",
            }:
                return False
            return self._str_arg_is_known_integer(
                node.left
            ) and self._str_arg_is_known_integer(node.right)
        return False

    def _is_integer_type_name(self, type_name: Any) -> bool:
        lowered = str(type_name).strip().lower()
        if lowered in {
            "int",
            "integer",
            "long",
            "short",
            "byte",
            "i8",
            "i16",
            "i32",
            "i64",
            "isize",
            "uint",
            "ulong",
            "ushort",
            "ubyte",
            "u8",
            "u16",
            "u32",
            "u64",
            "usize",
        }:
            return True
        if lowered.startswith("int") and lowered[3:].isdigit():
            return True
        return lowered.startswith("uint") and lowered[4:].isdigit()

    def builtin_str(self, args: list[ASTNode]) -> ir.Value:
        """Convert integer or float to string using sprintf."""
        if len(args) != 1:
            raise CodeGenError("str() expects exactly 1 argument")
        (value_arg,) = args
        value = self.generate_expr(value_arg)

        size = ir.Constant(ir.IntType(64), 32)
        buf = self.string_alloc(size, "str_buf")
        sprintf_fn = self.get_sprintf()

        if isinstance(value.type, (ir.FloatType, ir.DoubleType)):
            if isinstance(value.type, ir.FloatType):
                value = self.current_builder.fpext(value, ir.DoubleType(), name="f2d")
            fmt_str = self.create_string_constant("%g")
            self.current_builder.call(
                sprintf_fn, [buf, fmt_str, value], name="sprintf_call"
            )
        else:
            value = self.ensure_int64(value)
            fmt_str = self.create_string_constant("%lld")
            self.current_builder.call(
                sprintf_fn, [buf, fmt_str, value], name="sprintf_call"
            )

        return buf

    def builtin_startswith(self, args: list[ASTNode]) -> ir.Value:
        """Check if string starts with prefix."""
        if len(args) != 2:
            raise CodeGenError("startswith() expects (string, prefix)")
        string_arg, prefix_arg = args
        s_ptr = self.generate_expr(string_arg)
        prefix_ptr = self.generate_expr(prefix_arg)

        prefix_len = self.current_builder.call(
            self.get_strlen(), [prefix_ptr], name="prefix_len"
        )

        strncmp = self.get_strncmp()
        cmp_result = self.current_builder.call(
            strncmp, [s_ptr, prefix_ptr, prefix_len], name="strncmp_result"
        )
        zero = ir.Constant(ir.IntType(32), 0)
        is_match = self.current_builder.icmp_signed("==", cmp_result, zero)
        return self.current_builder.zext(is_match, ir.IntType(64), name="startswith")

    def builtin_endswith(self, args: list[ASTNode]) -> ir.Value:
        """Check if string ends with suffix."""
        if len(args) != 2:
            raise CodeGenError("endswith() expects (string, suffix)")
        string_arg, suffix_arg = args
        s_ptr = self.generate_expr(string_arg)
        suffix_ptr = self.generate_expr(suffix_arg)

        s_len = self.current_builder.call(self.get_strlen(), [s_ptr], name="s_len")
        suffix_len = self.current_builder.call(
            self.get_strlen(), [suffix_ptr], name="suffix_len"
        )

        suffix_fits = self.current_builder.icmp_signed("<=", suffix_len, s_len)

        ok_block = self.current_function.append_basic_block("endswith_ok")
        fail_block = self.current_function.append_basic_block("endswith_fail")
        merge_block = self.current_function.append_basic_block("endswith_merge")

        self.current_builder.cbranch(suffix_fits, ok_block, fail_block)

        self.current_builder.position_at_end(fail_block)
        false_val = ir.Constant(ir.IntType(64), 0)
        self.current_builder.branch(merge_block)
        fail_end = self.current_builder.block

        self.current_builder.position_at_end(ok_block)
        offset = self.current_builder.sub(s_len, suffix_len, name="end_offset")
        end_ptr = self.current_builder.gep(
            s_ptr,
            [self.current_builder.trunc(offset, ir.IntType(32))],
            name="end_ptr",
        )
        strncmp = self.get_strncmp()
        cmp_result = self.current_builder.call(
            strncmp, [end_ptr, suffix_ptr, suffix_len], name="strcmp_end"
        )
        zero32 = ir.Constant(ir.IntType(32), 0)
        is_match = self.current_builder.icmp_signed("==", cmp_result, zero32)
        true_val = self.current_builder.zext(is_match, ir.IntType(64))
        self.current_builder.branch(merge_block)
        ok_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        phi = self.current_builder.phi(ir.IntType(64), name="endswith_result")
        phi.add_incoming(false_val, fail_end)
        phi.add_incoming(true_val, ok_end)
        return phi

    def builtin_str_replace(self, args: list[ASTNode]) -> ir.Value:
        """Replace first occurrence of old with new."""
        if len(args) != 3:
            raise CodeGenError("str_replace() expects (string, old, new)")
        string_arg, old_arg, new_arg = args
        s_ptr = self.generate_expr(string_arg)
        old_ptr = self.generate_expr(old_arg)
        new_ptr = self.generate_expr(new_arg)

        found = self.current_builder.call(
            self.get_strstr(), [s_ptr, old_ptr], name="found_ptr"
        )
        null_ptr = ir.Constant(found.type, None)
        is_null = self.current_builder.icmp_unsigned("==", found, null_ptr)

        found_block = self.current_function.append_basic_block("replace_found")
        not_found_block = self.current_function.append_basic_block("replace_notfound")
        merge_block = self.current_function.append_basic_block("replace_merge")

        self.current_builder.cbranch(is_null, not_found_block, found_block)

        self.current_builder.position_at_end(not_found_block)
        s_len = self.current_builder.call(self.get_strlen(), [s_ptr], name="orig_len")
        copy_size = self.current_builder.add(
            s_len, ir.Constant(ir.IntType(64), 1), name="copy_size"
        )
        copy_buf = self.string_alloc(copy_size, "copy_buf")
        self.current_builder.call(self.get_memcpy(), [copy_buf, s_ptr, copy_size])
        self.current_builder.branch(merge_block)
        notfound_end = self.current_builder.block

        self.current_builder.position_at_end(found_block)
        s_len2 = self.current_builder.call(self.get_strlen(), [s_ptr], name="s_len")
        old_len = self.current_builder.call(
            self.get_strlen(), [old_ptr], name="old_len"
        )
        new_len = self.current_builder.call(
            self.get_strlen(), [new_ptr], name="new_len"
        )

        temp = self.current_builder.sub(s_len2, old_len, name="temp1")
        result_len = self.current_builder.add(temp, new_len, name="result_len")
        alloc_size = self.current_builder.add(
            result_len, ir.Constant(ir.IntType(64), 1), name="alloc_size"
        )
        result_buf = self.string_alloc(alloc_size, "result_buf")

        s_int = self.current_builder.ptrtoint(s_ptr, ir.IntType(64))
        found_int = self.current_builder.ptrtoint(found, ir.IntType(64))
        prefix_len = self.current_builder.sub(found_int, s_int, name="prefix_len")
        self.current_builder.call(self.get_memcpy(), [result_buf, s_ptr, prefix_len])

        dest1 = self.current_builder.gep(
            result_buf,
            [self.current_builder.trunc(prefix_len, ir.IntType(32))],
            name="dest1",
        )
        self.current_builder.call(self.get_memcpy(), [dest1, new_ptr, new_len])

        suffix_start = self.current_builder.gep(
            found,
            [self.current_builder.trunc(old_len, ir.IntType(32))],
            name="suffix_start",
        )
        suffix_len = self.current_builder.call(
            self.get_strlen(), [suffix_start], name="suffix_len"
        )
        dest2_offset = self.current_builder.add(prefix_len, new_len, name="dest2_off")
        dest2 = self.current_builder.gep(
            result_buf,
            [self.current_builder.trunc(dest2_offset, ir.IntType(32))],
            name="dest2",
        )
        copy_len = self.current_builder.add(
            suffix_len, ir.Constant(ir.IntType(64), 1), name="suffix_copy"
        )
        self.current_builder.call(self.get_memcpy(), [dest2, suffix_start, copy_len])
        self.current_builder.branch(merge_block)
        found_end = self.current_builder.block

        self.current_builder.position_at_end(merge_block)
        phi = self.current_builder.phi(
            ir.IntType(8).as_pointer(), name="replace_result"
        )
        phi.add_incoming(copy_buf, notfound_end)
        phi.add_incoming(result_buf, found_end)
        return phi
