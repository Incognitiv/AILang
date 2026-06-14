"""LLVM helpers for fixed-shape SQLite text binding."""

from __future__ import annotations

from typing import Any

from llvmlite import ir

BUF_CAP = 256
BUF_LAST = BUF_CAP - 1


def emit_sql_bind_text_i64_direct(
    e: Any,
    stmt_ptr: ir.Value,
    idx_i32: ir.Value,
    prefix: ir.Value,
    val: ir.Value,
    suffix: ir.Value,
) -> ir.Value:
    """Emit prefix + i64 + suffix into a fixed buffer, then sqlite3_bind_text."""
    char = ir.IntType(8)
    i64 = ir.IntType(64)
    char_ptr = char.as_pointer()
    buf_ty = ir.ArrayType(char, BUF_CAP)
    digits_ty = ir.ArrayType(char, 20)

    buf = e.codegen.alloca_in_entry_block(buf_ty, "sql_text_i64_buf")
    digits = e.codegen.alloca_in_entry_block(digits_ty, "sql_i64_digits")
    idx_ptr = e.codegen.alloca_in_entry_block(i64, "sql_text_i64_idx")
    digit_count_ptr = e.codegen.alloca_in_entry_block(i64, "sql_digit_count")
    mag_ptr = e.codegen.alloca_in_entry_block(i64, "sql_i64_mag")

    zero32 = ir.Constant(ir.IntType(32), 0)
    zero64 = ir.Constant(i64, 0)
    buf_ptr = e.builder.gep(buf, [zero32, zero32], name="sql_text_i64_ptr")
    digits_ptr = e.builder.gep(digits, [zero32, zero32], name="sql_digits_ptr")
    e.builder.store(zero64, idx_ptr)

    _append_cstr(e, prefix, buf_ptr, idx_ptr)
    _append_i64(e, val, buf_ptr, idx_ptr, digits_ptr, digit_count_ptr, mag_ptr)
    _append_cstr(e, suffix, buf_ptr, idx_ptr)
    _terminate(e, buf_ptr, idx_ptr)

    transient = e.builder.inttoptr(
        ir.Constant(i64, -1), char_ptr, name="sqlite_transient_i64"
    )
    result = e.builder.call(
        e.codegen.get_sqlite3_bind_text(),
        [stmt_ptr, idx_i32, buf_ptr, ir.Constant(ir.IntType(32), -1), transient],
        name="bind_text_i64_rc",
    )
    return e.builder.sext(result, i64, name="bind_text_i64_rc64")


def _unique(e: Any, stem: str) -> str:
    return e.codegen.module.get_unique_name(stem)


def _append_cstr(e: Any, src: ir.Value, buf_ptr: ir.Value, idx_ptr: ir.Value) -> None:
    i64 = ir.IntType(64)
    char = ir.IntType(8)
    src_i_ptr = e.codegen.alloca_in_entry_block(i64, _unique(e, "sql_copy_i"))
    e.builder.store(ir.Constant(i64, 0), src_i_ptr)

    cond = e.function.append_basic_block(_unique(e, "sql_copy_cond"))
    body = e.function.append_basic_block(_unique(e, "sql_copy_body"))
    done = e.function.append_basic_block(_unique(e, "sql_copy_done"))
    e.builder.branch(cond)

    e.builder.position_at_end(cond)
    src_i = e.builder.load(src_i_ptr, name="sql_copy_i_val")
    out_i = e.builder.load(idx_ptr, name="sql_copy_out_i")
    ch_ptr = e.builder.gep(src, [src_i], name="sql_copy_src")
    ch = e.builder.load(ch_ptr, name="sql_copy_ch")
    has_char = e.builder.icmp_unsigned("!=", ch, ir.Constant(char, 0))
    has_room = e.builder.icmp_unsigned("<", out_i, ir.Constant(i64, BUF_LAST))
    e.builder.cbranch(e.builder.and_(has_char, has_room), body, done)

    e.builder.position_at_end(body)
    dst = e.builder.gep(buf_ptr, [out_i], name="sql_copy_dst")
    e.builder.store(ch, dst)
    e.builder.store(e.builder.add(src_i, ir.Constant(i64, 1)), src_i_ptr)
    e.builder.store(e.builder.add(out_i, ir.Constant(i64, 1)), idx_ptr)
    e.builder.branch(cond)
    e.builder.position_at_end(done)


def _append_i64(
    e: Any,
    val: ir.Value,
    buf_ptr: ir.Value,
    idx_ptr: ir.Value,
    digits_ptr: ir.Value,
    digit_count_ptr: ir.Value,
    mag_ptr: ir.Value,
) -> None:
    i64 = ir.IntType(64)
    zero = ir.Constant(i64, 0)
    is_neg = e.builder.icmp_signed("<", val, zero, name="sql_i64_neg")
    neg_mag = e.builder.sub(zero, val, name="sql_i64_neg_mag")
    mag = e.builder.select(is_neg, neg_mag, val, name="sql_i64_mag_sel")
    e.builder.store(mag, mag_ptr)
    e.builder.store(zero, digit_count_ptr)

    sign_block = e.function.append_basic_block(_unique(e, "sql_i64_sign"))
    after_sign = e.function.append_basic_block(_unique(e, "sql_i64_after_sign"))
    e.builder.cbranch(is_neg, sign_block, after_sign)

    e.builder.position_at_end(sign_block)
    _append_byte(e, ir.Constant(ir.IntType(8), ord("-")), buf_ptr, idx_ptr)
    e.builder.branch(after_sign)
    e.builder.position_at_end(after_sign)

    is_zero = e.builder.icmp_unsigned(
        "==", e.builder.load(mag_ptr, name="sql_mag_zero_load"), zero
    )
    zero_block = e.function.append_basic_block(_unique(e, "sql_i64_zero"))
    digits_block = e.function.append_basic_block(_unique(e, "sql_i64_digits"))
    after_digits = e.function.append_basic_block(_unique(e, "sql_i64_after_digits"))
    e.builder.cbranch(is_zero, zero_block, digits_block)

    e.builder.position_at_end(zero_block)
    _append_byte(e, ir.Constant(ir.IntType(8), ord("0")), buf_ptr, idx_ptr)
    e.builder.branch(after_digits)

    e.builder.position_at_end(digits_block)
    _collect_digits(e, digits_ptr, digit_count_ptr, mag_ptr)
    _flush_digits_reverse(e, digits_ptr, digit_count_ptr, buf_ptr, idx_ptr)
    e.builder.branch(after_digits)
    e.builder.position_at_end(after_digits)


def _collect_digits(
    e: Any, digits_ptr: ir.Value, count_ptr: ir.Value, mag_ptr: ir.Value
) -> None:
    i64 = ir.IntType(64)
    char = ir.IntType(8)
    cond = e.function.append_basic_block(_unique(e, "sql_digits_cond"))
    body = e.function.append_basic_block(_unique(e, "sql_digits_body"))
    done = e.function.append_basic_block(_unique(e, "sql_digits_done"))
    e.builder.branch(cond)

    e.builder.position_at_end(cond)
    mag = e.builder.load(mag_ptr, name="sql_digits_mag")
    count = e.builder.load(count_ptr, name="sql_digits_count")
    has_mag = e.builder.icmp_unsigned("!=", mag, ir.Constant(i64, 0))
    has_room = e.builder.icmp_unsigned("<", count, ir.Constant(i64, 20))
    e.builder.cbranch(e.builder.and_(has_mag, has_room), body, done)

    e.builder.position_at_end(body)
    digit = e.builder.urem(mag, ir.Constant(i64, 10), name="sql_digit_mod")
    digit_i8 = e.builder.trunc(digit, char, name="sql_digit_i8")
    ascii_digit = e.builder.add(digit_i8, ir.Constant(char, ord("0")))
    slot = e.builder.gep(digits_ptr, [count], name="sql_digit_slot")
    e.builder.store(ascii_digit, slot)
    e.builder.store(e.builder.udiv(mag, ir.Constant(i64, 10)), mag_ptr)
    e.builder.store(e.builder.add(count, ir.Constant(i64, 1)), count_ptr)
    e.builder.branch(cond)
    e.builder.position_at_end(done)


def _flush_digits_reverse(
    e: Any,
    digits_ptr: ir.Value,
    count_ptr: ir.Value,
    buf_ptr: ir.Value,
    idx_ptr: ir.Value,
) -> None:
    i64 = ir.IntType(64)
    cond = e.function.append_basic_block(_unique(e, "sql_rev_cond"))
    body = e.function.append_basic_block(_unique(e, "sql_rev_body"))
    done = e.function.append_basic_block(_unique(e, "sql_rev_done"))
    e.builder.branch(cond)

    e.builder.position_at_end(cond)
    count = e.builder.load(count_ptr, name="sql_rev_count")
    e.builder.cbranch(
        e.builder.icmp_unsigned(">", count, ir.Constant(i64, 0)), body, done
    )

    e.builder.position_at_end(body)
    next_count = e.builder.sub(count, ir.Constant(i64, 1), name="sql_rev_next")
    e.builder.store(next_count, count_ptr)
    slot = e.builder.gep(digits_ptr, [next_count], name="sql_rev_slot")
    ch = e.builder.load(slot, name="sql_rev_ch")
    _append_byte(e, ch, buf_ptr, idx_ptr)
    e.builder.branch(cond)
    e.builder.position_at_end(done)


def _append_byte(e: Any, ch: ir.Value, buf_ptr: ir.Value, idx_ptr: ir.Value) -> None:
    i64 = ir.IntType(64)
    check = e.function.append_basic_block(_unique(e, "sql_byte_check"))
    store = e.function.append_basic_block(_unique(e, "sql_byte_store"))
    done = e.function.append_basic_block(_unique(e, "sql_byte_done"))
    e.builder.branch(check)

    e.builder.position_at_end(check)
    out_i = e.builder.load(idx_ptr, name="sql_byte_i")
    has_room = e.builder.icmp_unsigned("<", out_i, ir.Constant(i64, BUF_LAST))
    e.builder.cbranch(has_room, store, done)

    e.builder.position_at_end(store)
    dst = e.builder.gep(buf_ptr, [out_i], name="sql_byte_dst")
    e.builder.store(ch, dst)
    e.builder.store(e.builder.add(out_i, ir.Constant(i64, 1)), idx_ptr)
    e.builder.branch(done)
    e.builder.position_at_end(done)


def _terminate(e: Any, buf_ptr: ir.Value, idx_ptr: ir.Value) -> None:
    out_i = e.builder.load(idx_ptr, name="sql_text_i64_end_i")
    end = e.builder.gep(buf_ptr, [out_i], name="sql_text_i64_end")
    e.builder.store(ir.Constant(ir.IntType(8), 0), end)
