from pathlib import Path


def exact(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"FAIL-CLOSED: {path}: expected one anchor, got {count}")
    p.write_text(text.replace(old, new), encoding="utf-8")


exact(
    "stdlib/core/bigint.ail",
    '''pointer ailang_bigint_pow(pointer base, int exponent):
    if exponent < 0 then
        ailang_bigint_fail("negative integer exponent")
    end
    pointer result = ailang_bigint_from_int(1)
    pointer factor = ailang_bigint_clone(base)
    int e = exponent
    while e > 0 then
        if (e % 2) != 0 then
            pointer next = ailang_bigint_mul(result, factor)
            ailang_bigint_free(result)
            result = next
        end
        e = e / 2
        if e != 0 then
            pointer squared = ailang_bigint_mul(factor, factor)
            ailang_bigint_free(factor)
            factor = squared
        end
    end
    ailang_bigint_free(factor)
    return result
end''',
    '''pointer ailang_bigint_pow(pointer base, pointer exponent):
    if ailang_bigint_sign(exponent) < 0 then
        ailang_bigint_fail("negative integer exponent")
    end
    pointer result = ailang_bigint_from_int(1)
    pointer factor = ailang_bigint_clone(base)
    pointer e = ailang_bigint_clone(exponent)
    while ailang_bigint_sign(e) != 0 then
        if (ailang_bigint_limb(e, 0) & 1) != 0 then
            pointer next = ailang_bigint_mul(result, factor)
            ailang_bigint_free(result)
            result = next
        end
        pointer next_e = ailang_bigint_shr(e, 1)
        ailang_bigint_free(e)
        e = next_e
        if ailang_bigint_sign(e) != 0 then
            pointer squared = ailang_bigint_mul(factor, factor)
            ailang_bigint_free(factor)
            factor = squared
        end
    end
    ailang_bigint_free(e)
    ailang_bigint_free(factor)
    return result
end''',
)

exact(
    "stdlib/core/bigint.ail",
    '''pointer ailang_bigint_pow_take(pointer a, int exponent):
    pointer out = ailang_bigint_pow(a, exponent)
    ailang_bigint_free(a)
    return out
end''',
    '''pointer ailang_bigint_pow_take(pointer a, pointer exponent):
    pointer out = ailang_bigint_pow(a, exponent)
    ailang_bigint_free(a)
    ailang_bigint_free(exponent)
    return out
end''',
)

exact(
    "stdlib/core/bigint.ail",
    'ailang_bigint_fail("unbounded shift/exponent does not fit non-negative i64")',
    'ailang_bigint_fail("unbounded shift does not fit non-negative i64")',
)

exact(
    "source/codegen/bigint_runtime.py",
    'return self._adapter("_bigint_pow_func", "ailang_bigint_pow", p, [p, ir.IntType(64)])',
    'return self._adapter("_bigint_pow_func", "ailang_bigint_pow", p, [p, p])',
)

exact(
    "source/transpiler/expr_gen_binary_impl.py",
    '''    if op in ("**", "^", "<<", "shl", ">>", "shr"):
        # The count is also materialized as a BigInt, then checked to a
        # non-negative i64. This avoids any silent wide->i64 truncation.
        count = f"ailang_bigint_count_take({owned_bigint_expr(self, node.right)})"
        fn = "pow" if op in ("**", "^") else ("shl" if op in ("<<", "shl") else "shr")
        return f"ailang_bigint_{fn}_take({left}, {count})"''',
    '''    if op in ("**", "^"):
        exponent = owned_bigint_expr(self, node.right)
        return f"ailang_bigint_pow_take({left}, {exponent})"
    if op in ("<<", "shl", ">>", "shr"):
        # Shift counts remain a bounded machine quantity. Materialize as
        # BigInt first and narrow through the checked helper so no wide value
        # can silently truncate to i64.
        count = f"ailang_bigint_count_take({owned_bigint_expr(self, node.right)})"
        fn = "shl" if op in ("<<", "shl") else "shr"
        return f"ailang_bigint_{fn}_take({left}, {count})"''',
)

exact(
    "source/transpiler/expr_ops.py",
    '''        if op_lower in {"shl", "<<", "lshift", "shr", ">>", "rshift", "ushr", "**", "^", "power"}:
            left, own_left = self._bigint_operand(node.left)
            if op_lower == "ushr":
                if own_left:
                    self.builder.call(self.codegen._get_bigint_free(), [left])
                raise ExprGenError(
                    "logical right shift 'ushr' is undefined for unbounded integers; use >>"
                )
            count, owned_count = self._bigint_nonnegative_i64(node.right)
            if op_lower in {"**", "^", "power"}:
                result = self.builder.call(
                    self.codegen._get_bigint_pow(), [left, count], name="bigint_pow"
                )
            else:
                fn = (
                    self.codegen._get_bigint_shl()
                    if op_lower in {"shl", "<<", "lshift"}
                    else self.codegen._get_bigint_shr()
                )
                result = self.builder.call(fn, [left, count], name="bigint_shift")
            if own_left:
                self.builder.call(self.codegen._get_bigint_free(), [left])
            if owned_count is not None:
                self.builder.call(self.codegen._get_bigint_free(), [owned_count])
            return result''',
    '''        if op_lower in {"**", "^", "power"}:
            left, own_left = self._bigint_operand(node.left)
            exponent, own_exponent = self._bigint_operand(node.right)
            sign = self.builder.call(
                self.codegen._get_bigint_sign(), [exponent], name="bigint_exponent_sign"
            )
            negative = self.builder.icmp_signed(
                "<", sign, ir.Constant(ir.IntType(64), 0), name="bigint_exponent_negative"
            )
            fail = self.function.append_basic_block("bigint_exponent_negative_fail")
            ok = self.function.append_basic_block("bigint_exponent_nonnegative")
            self.builder.cbranch(negative, fail, ok)
            self.builder.position_at_end(fail)
            msg = self.codegen.create_string_constant(
                "Error: unbounded exponent cannot be negative!\\n"
            )
            self.builder.call(self.codegen.get_printf(), [msg])
            self.codegen._emit_safety_trap("negative unbounded exponent")
            self.builder.position_at_end(ok)
            result = self.builder.call(
                self.codegen._get_bigint_pow(), [left, exponent], name="bigint_pow"
            )
            if own_left:
                self.builder.call(self.codegen._get_bigint_free(), [left])
            if own_exponent:
                self.builder.call(self.codegen._get_bigint_free(), [exponent])
            return result

        if op_lower in {"shl", "<<", "lshift", "shr", ">>", "rshift", "ushr"}:
            left, own_left = self._bigint_operand(node.left)
            if op_lower == "ushr":
                if own_left:
                    self.builder.call(self.codegen._get_bigint_free(), [left])
                raise ExprGenError(
                    "logical right shift 'ushr' is undefined for unbounded integers; use >>"
                )
            count, owned_count = self._bigint_nonnegative_i64(node.right)
            fn = (
                self.codegen._get_bigint_shl()
                if op_lower in {"shl", "<<", "lshift"}
                else self.codegen._get_bigint_shr()
            )
            result = self.builder.call(fn, [left, count], name="bigint_shift")
            if own_left:
                self.builder.call(self.codegen._get_bigint_free(), [left])
            if owned_count is not None:
                self.builder.call(self.codegen._get_bigint_free(), [owned_count])
            return result''',
)

p = Path("source/transpiler/expr_ops.py")
text = p.read_text(encoding="utf-8")
for old, new in {
    "Error: unbounded shift/exponent must fit non-negative i64!\\n": "Error: unbounded shift must fit non-negative i64!\\n",
    "invalid unbounded shift/exponent": "invalid unbounded shift",
    "Error: shift/exponent cannot be negative!\\n": "Error: shift cannot be negative!\\n",
    "negative shift/exponent": "negative shift",
}.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"FAIL-CLOSED: expr_ops.py: {old!r}: expected one anchor, got {count}")
    text = text.replace(old, new)
p.write_text(text, encoding="utf-8")

assert not Path("source/runtime/ailang_bigint.c").exists()
assert "pointer ailang_bigint_pow(pointer base, pointer exponent)" in Path("stdlib/core/bigint.ail").read_text(encoding="utf-8")
assert '"ailang_bigint_pow", p, [p, p]' in Path("source/codegen/bigint_runtime.py").read_text(encoding="utf-8")
assert "ailang_bigint_pow_take({left}, {exponent})" in Path("source/transpiler/expr_gen_binary_impl.py").read_text(encoding="utf-8")
print("REPAIR_APPLIED")
