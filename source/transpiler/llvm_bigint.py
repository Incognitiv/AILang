"""LLVM helpers for AILang ``unbounded`` arbitrary-precision integers.

The language value is an owning pointer to the shared native sign+limb
runtime object.  This module centralizes the bridges between LLVM fixed-width
integers and that runtime so no call-site silently truncates through i64.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any

from llvmlite import ir


def is_unbounded_spec(codegen: Any, spec: object) -> bool:
    try:
        resolved = codegen._resolve_type_alias_spec(spec)
    except Exception:
        resolved = spec
    return parsed_type_to_str(resolved).strip().lower() == "unbounded"


def is_bigint_value(codegen: Any, value: ir.Value) -> bool:
    return codegen.is_bigint_type(value.type)


def bigint_from_decimal_literal(
    codegen: Any, builder: ir.IRBuilder, node: A.Number
) -> ir.Value:
    if node.is_float:
        raise TypeError("floating-point value cannot initialize unbounded integer")
    text = str(int(node.value))
    ptr = codegen.create_string_constant(text)
    return builder.call(codegen._get_bigint_from_decimal(), [ptr], name="bigint_lit")


def fixed_to_bigint(
    codegen: Any,
    builder: ir.IRBuilder,
    value: ir.Value,
    *,
    unsigned: bool | None = None,
) -> ir.Value:
    if is_bigint_value(codegen, value):
        return value
    if not isinstance(value.type, ir.IntType):
        raise TypeError(f"cannot convert {value.type} to unbounded")
    width = value.type.width
    if unsigned is None:
        unsigned = bool(codegen.is_unsigned_value(value))
    i64 = ir.IntType(64)
    if width <= 64:
        if width < 64:
            widened = (
                builder.zext(value, i64, name="bigint_zext")
                if unsigned
                else builder.sext(value, i64, name="bigint_sext")
            )
        else:
            widened = value
        if unsigned:
            return builder.call(
                codegen._get_bigint_from_u64(), [widened], name="bigint_from_u64"
            )
        return builder.call(
            codegen._get_bigint_from_int(), [widened], name="bigint_from_i64"
        )

    zero = ir.Constant(value.type, 0)
    negative: ir.Value
    magnitude: ir.Value
    if unsigned:
        negative = ir.Constant(ir.IntType(1), 0)
        magnitude = value
    else:
        negative = builder.icmp_signed("<", value, zero, name="bigint_neg")
        negated = builder.sub(zero, value, name="bigint_mag_neg")
        magnitude = builder.select(negative, negated, value, name="bigint_mag")

    words = (width + 63) // 64
    arr_ty = ir.ArrayType(i64, words)
    slot = codegen.alloca_in_entry_block(arr_ty, "bigint_words")
    i32 = ir.IntType(32)
    for idx in range(words):
        part = magnitude
        shift = idx * 64
        if shift:
            part = builder.lshr(
                part, ir.Constant(value.type, shift), name=f"bigint_word_shift_{idx}"
            )
        if width > 64:
            part = builder.trunc(part, i64, name=f"bigint_word_{idx}")
        out = builder.gep(slot, [ir.Constant(i32, 0), ir.Constant(i32, idx)])
        builder.store(part, out)
    data = builder.gep(slot, [ir.Constant(i32, 0), ir.Constant(i32, 0)])
    nonzero = builder.icmp_unsigned("!=", magnitude, zero, name="bigint_nonzero")
    sign_pos = ir.Constant(i64, 1)
    sign_neg = ir.Constant(i64, -1)
    sign_zero = ir.Constant(i64, 0)
    sign = builder.select(
        nonzero,
        builder.select(negative, sign_neg, sign_pos),
        sign_zero,
        name="bigint_sign_value",
    )
    return builder.call(
        codegen._get_bigint_from_words(),
        [sign, data, ir.Constant(i64, words)],
        name="bigint_from_words",
    )


def bigint_to_fixed(
    codegen: Any,
    builder: ir.IRBuilder,
    value: ir.Value,
    target: ir.IntType,
    *,
    unsigned: bool,
) -> ir.Value:
    if not is_bigint_value(codegen, value):
        raise TypeError("bigint_to_fixed requires unbounded value")
    i64 = ir.IntType(64)
    fit_fn = (
        codegen._get_bigint_fits_unsigned()
        if unsigned
        else codegen._get_bigint_fits_signed()
    )
    fits64 = builder.call(
        fit_fn, [value, ir.Constant(i64, target.width)], name="bigint_fits"
    )
    fits = builder.icmp_unsigned("!=", fits64, ir.Constant(i64, 0))
    fail = codegen.current_function.append_basic_block("bigint_cast_fail")
    ok = codegen.current_function.append_basic_block("bigint_cast_ok")
    builder.cbranch(fits, ok, fail)
    builder.position_at_end(fail)
    msg = codegen.create_string_constant(
        f"Error: unbounded integer does not fit {'u' if unsigned else 'i'}{target.width}!\\n"
    )
    builder.call(codegen.get_printf(), [msg])
    codegen._emit_safety_trap("unbounded integer conversion out of range")
    builder.position_at_end(ok)

    words = (target.width + 63) // 64
    result: ir.Value = ir.Constant(target, 0)
    for idx in range(words):
        word = builder.call(
            codegen._get_bigint_word_at(),
            [value, ir.Constant(i64, idx)],
            name=f"bigint_word_at_{idx}",
        )
        if target.width < 64:
            piece = builder.trunc(word, target, name=f"bigint_piece_{idx}")
        elif target.width == 64:
            piece = word
        else:
            piece = builder.zext(word, target, name=f"bigint_piece_{idx}")
            if idx:
                piece = builder.shl(
                    piece,
                    ir.Constant(target, idx * 64),
                    name=f"bigint_piece_shift_{idx}",
                )
        result = builder.or_(result, piece, name=f"bigint_join_{idx}")
    if not unsigned:
        sign64 = builder.call(codegen._get_bigint_sign(), [value], name="bigint_sign")
        neg = builder.icmp_signed("<", sign64, ir.Constant(i64, 0))
        negated = builder.sub(
            ir.Constant(target, 0), result, name="bigint_signed_value"
        )
        result = builder.select(neg, negated, result, name="bigint_fixed_value")
    codegen.set_signedness(result, not unsigned)
    return result


def expression_is_borrowed_bigint(node: A.ASTNode) -> bool:
    """Variables and field reads borrow existing storage; other bigint-valued
    expressions are produced as fresh owning runtime objects."""
    return isinstance(node, (A.Variable, A.FieldAccess, A.ThisExpr))


def free_if_owned_temp(
    codegen: Any,
    builder: ir.IRBuilder,
    node: A.ASTNode,
    value: ir.Value,
    *,
    forced_owned: bool = False,
) -> None:
    if not is_bigint_value(codegen, value):
        return
    if forced_owned or not expression_is_borrowed_bigint(node):
        builder.call(codegen._get_bigint_free(), [value])


def clone_if_borrowed(
    codegen: Any, builder: ir.IRBuilder, node: A.ASTNode, value: ir.Value
) -> ir.Value:
    if is_bigint_value(codegen, value) and expression_is_borrowed_bigint(node):
        return builder.call(codegen._get_bigint_clone(), [value], name="bigint_clone")
    return value
