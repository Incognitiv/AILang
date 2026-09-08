"""Runtime helpers for value-preserving iN/uN conversions at C boundaries."""
from __future__ import annotations


def _c_type(bits: int, unsigned: bool) -> str:
    if bits <= 64:
        return f"uint{bits}_t" if unsigned else f"int{bits}_t"
    if bits == 128:
        return "unsigned __int128" if unsigned else "__int128"
    return f"ailang_u{bits}" if unsigned else f"ailang_i{bits}"


def _emit_family(self, family_bits: int) -> None:
    o = self._output.append
    src_s = _c_type(family_bits, False)
    src_u = _c_type(family_bits, True)
    widths = [b for b in (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192) if b <= family_bits]

    for target_bits in widths:
        for target_unsigned in (False, True):
            target_name = f"{'u' if target_unsigned else 'i'}{target_bits}"
            target_c = _c_type(target_bits, target_unsigned)
            for source_unsigned, source_c, source_tag in (
                (False, src_s, "s"),
                (True, src_u, "u"),
            ):
                name = f"ailang_cast_{target_name}_from_{source_tag}{family_bits}"
                o(f"AILANG_UNUSED static {target_c} {name}({source_c} v) {{")
                checks: list[str] = []
                if target_unsigned:
                    if not source_unsigned:
                        checks.append("v < 0")
                    if target_bits < family_bits:
                        if source_unsigned:
                            checks.append(f"(v >> {target_bits}) != 0")
                        else:
                            checks.append(
                                f"((({src_u})v) >> {target_bits}) != 0"
                            )
                else:
                    if source_unsigned:
                        # Signed iN accepts only values below its sign bit.
                        checks.append(f"(v >> {target_bits - 1}) != 0")
                    elif target_bits < family_bits:
                        low = f"-((({source_c})1) << {target_bits - 1})"
                        high = f"((((({source_c})1) << {target_bits - 1})) - 1)"
                        checks.extend([f"v < {low}", f"v > {high}"])
                if checks:
                    cond = " || ".join(f"({c})" for c in checks)
                    o(
                        f'    if ({cond}) __ailang_safety_trap('
                        f'"integer value does not fit {target_name}");'
                    )
                o(f"    return ({target_c})v;")
                o("}")




def _emit_arithmetic_family(self, bits: int) -> None:
    """Emit backend-local checked arithmetic for the <=128 fixed ladder.

    These helpers are generated *by the C backend*; they are not an AILang
    runtime dependency.  The source-language semantics remain in AILang.
    """
    o = self._output.append
    for unsigned in (False, True):
        tag = f"{'u' if unsigned else 'i'}{bits}"
        ctype = _c_type(bits, unsigned)
        utype = _c_type(bits, True)
        o(f"AILANG_UNUSED static {ctype} ailang_safe_add_{tag}({ctype} a, {ctype} b) {{ {ctype} r; if (__builtin_add_overflow(a,b,&r)) __ailang_safety_trap(\"integer overflow in addition\"); return r; }}")
        o(f"AILANG_UNUSED static {ctype} ailang_safe_sub_{tag}({ctype} a, {ctype} b) {{ {ctype} r; if (__builtin_sub_overflow(a,b,&r)) __ailang_safety_trap(\"integer overflow in subtraction\"); return r; }}")
        o(f"AILANG_UNUSED static {ctype} ailang_safe_mul_{tag}({ctype} a, {ctype} b) {{ {ctype} r; if (__builtin_mul_overflow(a,b,&r)) __ailang_safety_trap(\"integer overflow in multiplication\"); return r; }}")
        if unsigned:
            o(f"AILANG_UNUSED static {ctype} ailang_safe_div_{tag}({ctype} a, {ctype} b) {{ if (b == 0) __ailang_safety_trap(\"division by zero\"); return a / b; }}")
            o(f"AILANG_UNUSED static {ctype} ailang_safe_mod_{tag}({ctype} a, {ctype} b) {{ if (b == 0) __ailang_safety_trap(\"modulo by zero\"); return a % b; }}")
        else:
            o(f"AILANG_UNUSED static {ctype} ailang_safe_div_{tag}({ctype} a, {ctype} b) {{ {ctype} minv=({ctype})((({utype})1) << {bits-1}); if (b == 0) __ailang_safety_trap(\"division by zero\"); if (a == minv && b == ({ctype})-1) __ailang_safety_trap(\"integer overflow in division\"); return a / b; }}")
            o(f"AILANG_UNUSED static {ctype} ailang_safe_mod_{tag}({ctype} a, {ctype} b) {{ {ctype} minv=({ctype})((({utype})1) << {bits-1}); if (b == 0) __ailang_safety_trap(\"modulo by zero\"); if (a == minv && b == ({ctype})-1) return ({ctype})0; return a % b; }}")
        o(f"AILANG_UNUSED static {ctype} ailang_safe_shl_{tag}({ctype} v, int64_t s) {{ if (s < 0 || s >= {bits}) __ailang_safety_trap(\"shift amount out of bounds\"); return ({ctype})(v << s); }}")
        o(f"AILANG_UNUSED static {ctype} ailang_safe_shr_{tag}({ctype} v, int64_t s) {{ if (s < 0 || s >= {bits}) __ailang_safety_trap(\"shift amount out of bounds\"); return ({ctype})(v >> s); }}")
        o(f"AILANG_UNUSED static {ctype} ailang_safe_pow_{tag}({ctype} base, int64_t exp) {{ if (exp < 0) __ailang_safety_trap(\"negative exponent for integer power\"); {ctype} out=({ctype})1; while (exp > 0) {{ if (exp & 1) out=ailang_safe_mul_{tag}(out,base); exp >>= 1; if (exp) base=ailang_safe_mul_{tag}(base,base); }} return out; }}")

def emit_runtime_fixed_int_casts(self) -> None:
    """Emit checked conversion helpers used by call/return expression contexts."""
    o = self._output.append
    o("/* Fixed integer value-conversion contract (function/callback boundaries). */")
    _emit_family(self, 64)
    for bits in (8, 16, 32, 64):
        _emit_arithmetic_family(self, bits)
    # Keep simple <=64-bit programs buildable on compilers without __int128.
    o("#if defined(__SIZEOF_INT128__)")
    _emit_family(self, 128)
    _emit_arithmetic_family(self, 128)
    o('AILANG_UNUSED static int64_t ailang_narrow_i64_i128(__int128 v) { if (v < (__int128)INT64_MIN || v > (__int128)INT64_MAX) __ailang_safety_trap("integer value does not fit signed 64-bit boundary"); return (int64_t)v; }')
    o('AILANG_UNUSED static int64_t ailang_narrow_i64_u128(unsigned __int128 v) { if (v > (unsigned __int128)INT64_MAX) __ailang_safety_trap("integer value does not fit signed 64-bit boundary"); return (int64_t)v; }')
    o("#endif")
    if self._needs.wide_ints:
        _emit_family(self, 8192)
    o("")
