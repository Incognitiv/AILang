"""Extracted responsibilities for :class:`BuiltinStringEmitter`."""

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


class BuiltinStringConversionMixin:
    def _try_emit_cached_strlen(self: Any, string_arg: ASTNode) -> ir.Value | None:
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

    def _try_emit_virtual_strlen(self: Any, string_arg: ASTNode) -> ir.Value | None:
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
        if value.type.width > 64:
            wide_text = self.wide_int_to_decimal_string(
                value, self.is_unsigned_value(value)
            )
            tail_len = self.current_builder.call(
                self.get_strlen(), [wide_text], name="virtual_wide_strlen"
            )
        else:
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

    def _str_arg_is_known_integer(self: Any, node: ASTNode) -> bool:
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

    def _is_integer_type_name(self: Any, type_name: Any) -> bool:
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

    def builtin_str(self: Any, args: list[ASTNode]) -> ir.Value:
        """Convert integer or float to string using sprintf."""
        if len(args) != 1:
            raise CodeGenError("str() expects exactly 1 argument")
        (value_arg,) = args
        value = self.generate_expr(value_arg)

        if self.is_bigint_type(value.type):
            raw = self.current_builder.call(
                self._get_bigint_to_decimal(), [value], name="bigint_decimal_raw"
            )
            raw_len = self.current_builder.call(
                self.get_strlen(), [raw], name="bigint_decimal_len"
            )
            size = self.current_builder.add(raw_len, ir.Constant(ir.IntType(64), 1))
            buf = self.string_alloc(size, "bigint_str_buf")
            self.current_builder.call(self.get_memcpy(), [buf, raw, size])
            self.current_builder.call(self._get_free(), [raw])
            return buf

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
            if isinstance(value.type, ir.IntType) and value.type.width > 64:
                return self.wide_int_to_decimal_string(
                    value, self.is_unsigned_value(value)
                )
            value = self.ensure_int64(value)
            fmt_str = self.create_string_constant("%lld")
            self.current_builder.call(
                sprintf_fn, [buf, fmt_str, value], name="sprintf_call"
            )

        return buf

    def builtin_startswith(self: Any, args: list[ASTNode]) -> ir.Value:
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

    def builtin_endswith(self: Any, args: list[ASTNode]) -> ir.Value:
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

    def builtin_str_escape_json(self: Any, args: list[ASTNode]) -> ir.Value:
        """Escape a string for JSON with one allocation and no per-byte strings."""
        if len(args) != 1:
            raise CodeGenError("str_escape_json() expects exactly 1 argument")
        (string_arg,) = args
        src = self.generate_expr(string_arg)
        i8 = ir.IntType(8)
        i64 = ir.IntType(64)
        zero = ir.Constant(i64, 0)
        one = ir.Constant(i64, 1)
        two = ir.Constant(i64, 2)
        length = self.current_builder.call(self.get_strlen(), [src], name="jsonesc_len")
        capacity = self.current_builder.add(
            self.current_builder.mul(length, two, name="jsonesc_2n"),
            one,
            name="jsonesc_cap",
        )
        out = self.string_alloc(capacity, "jsonesc_out")
        in_slot = self.current_builder.alloca(i64, name="jsonesc_i_slot")
        out_slot = self.current_builder.alloca(i64, name="jsonesc_j_slot")
        self.current_builder.store(zero, in_slot)
        self.current_builder.store(zero, out_slot)

        func = self.current_function
        header = func.append_basic_block("jsonesc_hdr")
        body = func.append_basic_block("jsonesc_body")
        escaped = func.append_basic_block("jsonesc_escaped")
        plain = func.append_basic_block("jsonesc_plain")
        merge = func.append_basic_block("jsonesc_merge")
        done = func.append_basic_block("jsonesc_done")
        self.current_builder.branch(header)

        self.current_builder.position_at_end(header)
        in_i = self.current_builder.load(in_slot, name="jsonesc_i")
        more = self.current_builder.icmp_unsigned(
            "<", in_i, length, name="jsonesc_more"
        )
        self.current_builder.cbranch(more, body, done)

        self.current_builder.position_at_end(body)
        ch_ptr = self.current_builder.gep(src, [in_i], name="jsonesc_srcp")
        ch = self.current_builder.load(ch_ptr, name="jsonesc_ch")
        quote = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 34))
        slash = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 92))
        nl = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 10))
        cr = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 13))
        tab = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 9))
        backspace = self.current_builder.icmp_unsigned("==", ch, ir.Constant(i8, 8))
        needs = self.current_builder.or_(quote, slash)
        needs = self.current_builder.or_(needs, nl)
        needs = self.current_builder.or_(needs, cr)
        needs = self.current_builder.or_(needs, tab)
        needs = self.current_builder.or_(needs, backspace)
        self.current_builder.cbranch(needs, escaped, plain)

        self.current_builder.position_at_end(escaped)
        out_i_e = self.current_builder.load(out_slot, name="jsonesc_je")
        slash_ptr = self.current_builder.gep(out, [out_i_e], name="jsonesc_slashp")
        self.current_builder.store(ir.Constant(i8, 92), slash_ptr)
        code = self.current_builder.select(quote, ir.Constant(i8, 34), ch)
        code = self.current_builder.select(slash, ir.Constant(i8, 92), code)
        code = self.current_builder.select(nl, ir.Constant(i8, 110), code)
        code = self.current_builder.select(cr, ir.Constant(i8, 114), code)
        code = self.current_builder.select(tab, ir.Constant(i8, 116), code)
        code = self.current_builder.select(backspace, ir.Constant(i8, 98), code)
        code_pos = self.current_builder.add(out_i_e, one, name="jsonesc_codepos")
        code_ptr = self.current_builder.gep(out, [code_pos], name="jsonesc_codep")
        self.current_builder.store(code, code_ptr)
        self.current_builder.store(
            self.current_builder.add(out_i_e, two, name="jsonesc_j2"), out_slot
        )
        self.current_builder.branch(merge)

        self.current_builder.position_at_end(plain)
        out_i_p = self.current_builder.load(out_slot, name="jsonesc_jp")
        plain_ptr = self.current_builder.gep(out, [out_i_p], name="jsonesc_dstp")
        self.current_builder.store(ch, plain_ptr)
        self.current_builder.store(
            self.current_builder.add(out_i_p, one, name="jsonesc_j1"), out_slot
        )
        self.current_builder.branch(merge)

        self.current_builder.position_at_end(merge)
        self.current_builder.store(
            self.current_builder.add(in_i, one, name="jsonesc_i1"), in_slot
        )
        self.current_builder.branch(header)

        self.current_builder.position_at_end(done)
        final_j = self.current_builder.load(out_slot, name="jsonesc_final_j")
        end_ptr = self.current_builder.gep(out, [final_j], name="jsonesc_end")
        self.current_builder.store(ir.Constant(i8, 0), end_ptr)
        return out

    def builtin_str_replace(self: Any, args: list[ASTNode]) -> ir.Value:
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
