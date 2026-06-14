"""CodeGen bigint string-format helpers mixin."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class _CodeGenBigIntFormatMixin:
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
