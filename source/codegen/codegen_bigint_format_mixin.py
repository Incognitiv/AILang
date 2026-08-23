"""CodeGen bigint string-format helpers mixin."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class _CodeGenBigIntFormatMixin:
    def wide_int_to_decimal_string(
        self: Any, value: ir.Value, is_unsigned: bool
    ) -> ir.Value:
        """Convert a fixed-width integer wider than 64 bits to decimal text.

        The conversion is performed in the integer's actual LLVM width.  It
        never routes through i64/f64, so i128..i8192 values retain their full
        range.  Signed minimum values are handled with modulo subtraction
        (0 - value), interpreted as an unsigned magnitude.
        """
        if not isinstance(value.type, ir.IntType):
            raise TypeError("wide_int_to_decimal_string expects an integer")

        builder = self.current_builder
        int_type = value.type
        width = int_type.width
        i1 = ir.IntType(1)
        i8 = ir.IntType(8)
        i32 = ir.IntType(32)
        i64 = ir.IntType(64)
        zero = ir.Constant(int_type, 0)
        ten = ir.Constant(int_type, 10)

        # ceil(bits * log10(2)) + optional sign + NUL + margin.
        max_digits = (width * 30103 + 99999) // 100000 + 1
        total = max_digits + 2
        out_raw = self.string_alloc(ir.Constant(i64, total), "wide_dec_buf")
        out = builder.bitcast(out_raw, i8.as_pointer())
        rev_ty = ir.ArrayType(i8, max_digits)
        rev = builder.alloca(rev_ty, name="wide_dec_rev")
        count_ptr = builder.alloca(i32, name="wide_dec_count")
        builder.store(ir.Constant(i32, 0), count_ptr)

        if is_unsigned:
            is_negative = ir.Constant(i1, 0)
            magnitude = value
        else:
            is_negative = builder.icmp_signed("<", value, zero, name="wide_dec_neg")
            neg_mag = builder.sub(zero, value, name="wide_dec_neg_mag")
            magnitude = builder.select(
                is_negative, neg_mag, value, name="wide_dec_magnitude"
            )

        zero_block = self.current_function.append_basic_block("wide_dec_zero")
        loop_block = self.current_function.append_basic_block("wide_dec_loop")
        after_digits = self.current_function.append_basic_block("wide_dec_after_digits")
        is_zero = builder.icmp_unsigned("==", magnitude, zero, name="wide_dec_is_zero")
        builder.cbranch(is_zero, zero_block, loop_block)

        builder.position_at_end(zero_block)
        zero_ptr = builder.gep(
            rev, [ir.Constant(i32, 0), ir.Constant(i32, 0)], name="wide_dec_zero_ptr"
        )
        builder.store(ir.Constant(i8, ord("0")), zero_ptr)
        builder.store(ir.Constant(i32, 1), count_ptr)
        builder.branch(after_digits)

        builder.position_at_end(loop_block)
        cond_block = self.current_function.append_basic_block("wide_dec_digit_cond")
        body_block = self.current_function.append_basic_block("wide_dec_digit_body")
        builder.branch(cond_block)

        builder.position_at_end(cond_block)
        mag_phi = builder.phi(int_type, name="wide_dec_mag")
        mag_phi.add_incoming(magnitude, loop_block)
        more = builder.icmp_unsigned("!=", mag_phi, zero, name="wide_dec_more")
        builder.cbranch(more, body_block, after_digits)

        builder.position_at_end(body_block)
        q = builder.udiv(mag_phi, ten, name="wide_dec_q")
        r = builder.urem(mag_phi, ten, name="wide_dec_r")
        digit = builder.trunc(r, i8, name="wide_dec_digit")
        digit_ch = builder.add(digit, ir.Constant(i8, ord("0")), name="wide_dec_ch")
        count = builder.load(count_ptr, name="wide_dec_count_v")
        dst = builder.gep(rev, [ir.Constant(i32, 0), count], name="wide_dec_rev_ptr")
        builder.store(digit_ch, dst)
        builder.store(builder.add(count, ir.Constant(i32, 1)), count_ptr)
        body_end = builder.block
        builder.branch(cond_block)
        mag_phi.add_incoming(q, body_end)

        builder.position_at_end(after_digits)
        count = builder.load(count_ptr, name="wide_dec_digits")
        sign_len = builder.select(
            is_negative, ir.Constant(i32, 1), ir.Constant(i32, 0), name="wide_dec_sign_len"
        )
        with builder.if_then(is_negative):
            builder.store(ir.Constant(i8, ord("-")), out)

        idx_ptr = builder.alloca(i32, name="wide_dec_out_i")
        builder.store(ir.Constant(i32, 0), idx_ptr)
        rev_cond = self.current_function.append_basic_block("wide_dec_rev_cond")
        rev_body = self.current_function.append_basic_block("wide_dec_rev_body")
        rev_done = self.current_function.append_basic_block("wide_dec_rev_done")
        builder.branch(rev_cond)

        builder.position_at_end(rev_cond)
        idx = builder.load(idx_ptr, name="wide_dec_i")
        not_done = builder.icmp_unsigned("<", idx, count, name="wide_dec_copy_more")
        builder.cbranch(not_done, rev_body, rev_done)

        builder.position_at_end(rev_body)
        src_idx = builder.sub(builder.sub(count, ir.Constant(i32, 1)), idx)
        src = builder.gep(rev, [ir.Constant(i32, 0), src_idx], name="wide_dec_src")
        dst_idx = builder.add(sign_len, idx, name="wide_dec_dst_i")
        dst = builder.gep(out, [dst_idx], name="wide_dec_dst")
        builder.store(builder.load(src, name="wide_dec_src_ch"), dst)
        builder.store(builder.add(idx, ir.Constant(i32, 1)), idx_ptr)
        builder.branch(rev_cond)

        builder.position_at_end(rev_done)
        final_len = builder.add(sign_len, count, name="wide_dec_len")
        term = builder.gep(out, [final_len], name="wide_dec_term")
        builder.store(ir.Constant(i8, 0), term)
        return out

    def bigint_to_hex_string(self: Any, value: ir.Value) -> ir.Value:
        """Convert integer to hexadecimal string (0x prefix, minimal width)."""
        width = value.type.width
        digits = (width + 3) // 4
        total = digits + 3
        buf_raw = self.string_alloc(ir.Constant(ir.IntType(64), total), "hex_buf")
        buf = self.current_builder.bitcast(buf_raw, ir.IntType(8).as_pointer())
        i1 = ir.IntType(1)
        i8 = ir.IntType(8)
        i32 = ir.IntType(32)

        self.current_builder.store(ir.Constant(ir.IntType(8), ord("0")), buf)
        x_ptr = self.current_builder.gep(buf, [ir.Constant(ir.IntType(32), 1)])
        self.current_builder.store(ir.Constant(ir.IntType(8), ord("x")), x_ptr)

        started = ir.Constant(i1, 0)
        pos = ir.Constant(i32, 2)
        for i in range(digits):
            shift_amt = (digits - 1 - i) * 4
            if shift_amt > 0:
                shifted = self.current_builder.lshr(
                    value, ir.Constant(value.type, shift_amt), name=f"hex_shift_{i}"
                )
            else:
                shifted = value
            nib = self.current_builder.trunc(shifted, i8, name=f"hex_nib_{i}")
            nib = self.current_builder.and_(nib, ir.Constant(i8, 0xF))
            is_digit = self.current_builder.icmp_unsigned("<", nib, ir.Constant(i8, 10))
            digit_char = self.current_builder.select(
                is_digit,
                self.current_builder.add(nib, ir.Constant(i8, ord("0"))),
                self.current_builder.add(nib, ir.Constant(i8, ord("A") - 10)),
            )
            nonzero = self.current_builder.icmp_unsigned(
                "!=", nib, ir.Constant(i8, 0), name=f"hex_nz_{i}"
            )
            emit_this = (
                ir.Constant(i1, 1)
                if i == digits - 1
                else self.current_builder.or_(started, nonzero, name=f"hex_emit_{i}")
            )
            dst_ptr = self.current_builder.gep(buf, [pos], name=f"hex_dst_{i}")
            self.current_builder.store(digit_char, dst_ptr)
            pos = self.current_builder.add(
                pos,
                self.current_builder.select(
                    emit_this, ir.Constant(i32, 1), ir.Constant(i32, 0)
                ),
                name=f"hex_pos_{i}",
            )
            started = self.current_builder.or_(started, nonzero, name=f"hex_st_{i}")
        term_ptr = self.current_builder.gep(buf, [pos])
        self.current_builder.store(ir.Constant(i8, 0), term_ptr)
        return buf

    def bigint_to_bin_string(self: Any, value: ir.Value) -> ir.Value:
        """Convert integer to binary string (0b prefix, minimal width)."""
        width = value.type.width
        digits = width
        total = digits + 3
        buf_raw = self.string_alloc(ir.Constant(ir.IntType(64), total), "bin_buf")
        buf = self.current_builder.bitcast(buf_raw, ir.IntType(8).as_pointer())
        i1 = ir.IntType(1)
        i8 = ir.IntType(8)
        i32 = ir.IntType(32)

        self.current_builder.store(ir.Constant(ir.IntType(8), ord("0")), buf)
        b_ptr = self.current_builder.gep(buf, [ir.Constant(ir.IntType(32), 1)])
        self.current_builder.store(ir.Constant(ir.IntType(8), ord("b")), b_ptr)

        started = ir.Constant(i1, 0)
        pos = ir.Constant(i32, 2)
        for i in range(digits):
            shift_amt = digits - 1 - i
            if shift_amt > 0:
                shifted = self.current_builder.lshr(
                    value, ir.Constant(value.type, shift_amt), name=f"bin_shift_{i}"
                )
            else:
                shifted = value
            bit = self.current_builder.trunc(shifted, i1, name=f"bin_bit_{i}")
            bit8 = self.current_builder.zext(bit, i8)
            bit_char = self.current_builder.add(bit8, ir.Constant(i8, ord("0")))
            emit_this = (
                ir.Constant(i1, 1)
                if i == digits - 1
                else self.current_builder.or_(started, bit, name=f"bin_emit_{i}")
            )
            dst_ptr = self.current_builder.gep(buf, [pos], name=f"bin_dst_{i}")
            self.current_builder.store(bit_char, dst_ptr)
            pos = self.current_builder.add(
                pos,
                self.current_builder.select(
                    emit_this, ir.Constant(i32, 1), ir.Constant(i32, 0)
                ),
                name=f"bin_pos_{i}",
            )
            started = self.current_builder.or_(started, bit, name=f"bin_st_{i}")
        term_ptr = self.current_builder.gep(buf, [pos])
        self.current_builder.store(ir.Constant(i8, 0), term_ptr)
        return buf

    def bigint_to_oct_string(self: Any, value: ir.Value) -> ir.Value:
        """Convert integer to octal string (0o prefix, minimal width)."""
        width = value.type.width
        digits = (width + 2) // 3
        total = digits + 3
        buf_raw = self.string_alloc(ir.Constant(ir.IntType(64), total), "oct_buf")
        buf = self.current_builder.bitcast(buf_raw, ir.IntType(8).as_pointer())
        i1 = ir.IntType(1)
        i8 = ir.IntType(8)
        i32 = ir.IntType(32)

        self.current_builder.store(ir.Constant(ir.IntType(8), ord("0")), buf)
        o_ptr = self.current_builder.gep(buf, [ir.Constant(ir.IntType(32), 1)])
        self.current_builder.store(ir.Constant(ir.IntType(8), ord("o")), o_ptr)

        started = ir.Constant(i1, 0)
        pos = ir.Constant(i32, 2)
        for i in range(digits):
            shift_amt = (digits - 1 - i) * 3
            if shift_amt > 0:
                shifted = self.current_builder.lshr(
                    value, ir.Constant(value.type, shift_amt), name=f"oct_shift_{i}"
                )
            else:
                shifted = value
            tri = self.current_builder.trunc(shifted, i8, name=f"oct_tri_{i}")
            tri = self.current_builder.and_(tri, ir.Constant(i8, 0x7))
            tri_char = self.current_builder.add(tri, ir.Constant(i8, ord("0")))
            nonzero = self.current_builder.icmp_unsigned(
                "!=", tri, ir.Constant(i8, 0), name=f"oct_nz_{i}"
            )
            emit_this = (
                ir.Constant(i1, 1)
                if i == digits - 1
                else self.current_builder.or_(started, nonzero, name=f"oct_emit_{i}")
            )
            dst_ptr = self.current_builder.gep(buf, [pos], name=f"oct_dst_{i}")
            self.current_builder.store(tri_char, dst_ptr)
            pos = self.current_builder.add(
                pos,
                self.current_builder.select(
                    emit_this, ir.Constant(i32, 1), ir.Constant(i32, 0)
                ),
                name=f"oct_pos_{i}",
            )
            started = self.current_builder.or_(started, nonzero, name=f"oct_st_{i}")
        term_ptr = self.current_builder.gep(buf, [pos])
        self.current_builder.store(ir.Constant(i8, 0), term_ptr)
        return buf
