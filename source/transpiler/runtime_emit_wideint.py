"""C23 fixed-width wide integer runtime helpers."""
from __future__ import annotations


def emit_runtime_wideint(self) -> None:
    if not self._needs.wide_ints:
        return
    o = self._output.append
    o("/* AILang 256..8192-bit safety + formatting helpers */")
    # Keep overflow semantics independent of compiler-specific builtins.
    # Clang rejects __builtin_*_overflow for signed _BitInt > 128 anyway.
    o("#define AILANG_WIDE_SAFE_BINOP_UNSIGNED(TYPE, NAME) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_add_##NAME(TYPE a, TYPE b) { TYPE maxv=(TYPE)~(TYPE)0; if (a > maxv - b) __ailang_safety_trap(\"wide integer overflow in addition\"); return (TYPE)(a+b); } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_sub_##NAME(TYPE a, TYPE b) { if (a < b) __ailang_safety_trap(\"wide integer overflow in subtraction\"); return (TYPE)(a-b); } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_mul_##NAME(TYPE a, TYPE b) { TYPE maxv=(TYPE)~(TYPE)0; if (b != 0 && a > maxv / b) __ailang_safety_trap(\"wide integer overflow in multiplication\"); return (TYPE)(a*b); }")
    o("")
    o("#define AILANG_WIDE_SAFE_BINOP_SIGNED(TYPE, UTYPE, NAME, BITS) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_add_##NAME(TYPE a, TYPE b) { TYPE maxv=(TYPE)((((UTYPE)1)<<((BITS)-1))-(UTYPE)1); TYPE minv=(TYPE)(-maxv-(TYPE)1); if ((b > 0 && a > maxv-b) || (b < 0 && a < minv-b)) __ailang_safety_trap(\"wide integer overflow in addition\"); return (TYPE)(a+b); } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_sub_##NAME(TYPE a, TYPE b) { TYPE maxv=(TYPE)((((UTYPE)1)<<((BITS)-1))-(UTYPE)1); TYPE minv=(TYPE)(-maxv-(TYPE)1); if ((b > 0 && a < minv+b) || (b < 0 && a > maxv+b)) __ailang_safety_trap(\"wide integer overflow in subtraction\"); return (TYPE)(a-b); } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_mul_##NAME(TYPE a, TYPE b) { TYPE maxv=(TYPE)((((UTYPE)1)<<((BITS)-1))-(UTYPE)1); TYPE minv=(TYPE)(-maxv-(TYPE)1); if (a != 0 && b != 0) { if ((a == (TYPE)-1 && b == minv) || (b == (TYPE)-1 && a == minv)) __ailang_safety_trap(\"wide integer overflow in multiplication\"); if (a > 0) { if ((b > 0 && a > maxv/b) || (b < 0 && b < minv/a)) __ailang_safety_trap(\"wide integer overflow in multiplication\"); } else { if ((b > 0 && a < minv/b) || (b < 0 && a < maxv/b)) __ailang_safety_trap(\"wide integer overflow in multiplication\"); } } return (TYPE)(a*b); }")
    o("")
    o("#define AILANG_WIDE_DIV_UNSIGNED(TYPE, NAME) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_div_##NAME(TYPE a, TYPE b) { if (b == 0) __ailang_safety_trap(\"division by zero\"); return a / b; } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_mod_##NAME(TYPE a, TYPE b) { if (b == 0) __ailang_safety_trap(\"modulo by zero\"); return a % b; }")
    o("")
    o("#define AILANG_WIDE_DIV_SIGNED(TYPE, UTYPE, NAME, BITS) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_div_##NAME(TYPE a, TYPE b) { TYPE minv = (TYPE)(((UTYPE)1) << ((BITS)-1)); if (b == 0) __ailang_safety_trap(\"division by zero\"); if (a == minv && b == (TYPE)-1) __ailang_safety_trap(\"wide integer overflow in division\"); return a / b; } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_mod_##NAME(TYPE a, TYPE b) { TYPE minv = (TYPE)(((UTYPE)1) << ((BITS)-1)); if (b == 0) __ailang_safety_trap(\"modulo by zero\"); if (a == minv && b == (TYPE)-1) return (TYPE)0; return a % b; }")
    o("")
    o("#define AILANG_WIDE_SHIFT(TYPE, NAME, BITS) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_shl_##NAME(TYPE v, int64_t s) { if (s < 0 || s >= (BITS)) __ailang_safety_trap(\"wide shift amount out of bounds\"); return v << s; } \\")
    o("AILANG_UNUSED static TYPE ailang_safe_shr_##NAME(TYPE v, int64_t s) { if (s < 0 || s >= (BITS)) __ailang_safety_trap(\"wide shift amount out of bounds\"); return v >> s; }")
    o("")
    o("#define AILANG_WIDE_POW(TYPE, NAME) \\")
    o("AILANG_UNUSED static TYPE ailang_safe_pow_##NAME(TYPE base, int64_t exp) { if (exp < 0) __ailang_safety_trap(\"negative exponent for integer power\"); TYPE out=(TYPE)1; while (exp > 0) { if (exp & 1) out = ailang_safe_mul_##NAME(out, base); exp >>= 1; if (exp) base = ailang_safe_mul_##NAME(base, base); } return out; }")
    o("")
    for bits in (256, 512, 1024, 2048, 4096, 8192):
        for prefix in ("i", "u"):
            suffix = f"{prefix}{bits}"
            ctype = f"ailang_{suffix}"
            if prefix == "u":
                o(f"AILANG_WIDE_SAFE_BINOP_UNSIGNED({ctype}, {suffix})")
                o(f"AILANG_WIDE_DIV_UNSIGNED({ctype}, {suffix})")
            else:
                o(f"AILANG_WIDE_SAFE_BINOP_SIGNED({ctype}, ailang_u{bits}, {suffix}, {bits})")
                o(f"AILANG_WIDE_DIV_SIGNED({ctype}, ailang_u{bits}, {suffix}, {bits})")
            o(f"AILANG_WIDE_SHIFT({ctype}, {suffix}, {bits})")
            o(f"AILANG_WIDE_POW({ctype}, {suffix})")
            if prefix == "u":
                o(f'AILANG_UNUSED static int64_t ailang_narrow_i64_{suffix}({ctype} v) {{ if (v > ({ctype})INT64_MAX) __ailang_safety_trap("integer value does not fit signed 64-bit boundary"); return (int64_t)v; }}')
            else:
                o(f'AILANG_UNUSED static int64_t ailang_narrow_i64_{suffix}({ctype} v) {{ if (v < ({ctype})INT64_MIN || v > ({ctype})INT64_MAX) __ailang_safety_trap("integer value does not fit signed 64-bit boundary"); return (int64_t)v; }}')
    o("")
    o("#ifndef AILANG_FREESTANDING")
    o("#define AILANG_WIDE_WRITE_UNSIGNED(TYPE, NAME) \\")
    o("AILANG_UNUSED static void ailang_write_##NAME(FILE *f, TYPE v) { char buf[sizeof(TYPE)*3u + 4u]; size_t i=0; do { unsigned d=(unsigned)(v % (TYPE)10); buf[i++]=(char)('0'+d); v /= (TYPE)10; } while (v != 0); while (i) fputc(buf[--i], f); }")
    o("#define AILANG_WIDE_WRITE_SIGNED(TYPE, UTYPE, NAME) \\")
    o("AILANG_UNUSED static void ailang_write_##NAME(FILE *f, TYPE v) { UTYPE mag; if (v < 0) { fputc('-', f); mag=(UTYPE)(-(v + (TYPE)1)); mag += (UTYPE)1; } else { mag=(UTYPE)v; } char buf[sizeof(TYPE)*3u + 4u]; size_t i=0; do { unsigned d=(unsigned)(mag % (UTYPE)10); buf[i++]=(char)('0'+d); mag /= (UTYPE)10; } while (mag != 0); while (i) fputc(buf[--i], f); }")
    for bits in (256, 512, 1024, 2048, 4096, 8192):
        o(f"AILANG_WIDE_WRITE_SIGNED(ailang_i{bits}, ailang_u{bits}, i{bits})")
        o(f"AILANG_WIDE_WRITE_UNSIGNED(ailang_u{bits}, u{bits})")

    # Materializing formatting helpers. These return request-arena strings,
    # matching the lifetime contract of str()/hex()/bin()/oct() for i64.
    for bits in (256, 512, 1024, 2048, 4096, 8192):
        for prefix in ("i", "u"):
            suffix = f"{prefix}{bits}"
            ctype = f"ailang_{suffix}"
            utype = f"ailang_u{bits}"
            cap10 = bits * 30103 // 100000 + 6
            if prefix == "i":
                o(f"AILANG_UNUSED static char *ailang_str_{suffix}({ctype} v) {{")
                o(f"    char *out=(char*)ailang_request_alloc({cap10}u); if (!out) return NULL;")
                o(f"    char tmp[{cap10}]; size_t i=0,j=0; {utype} mag;")
                o(f"    if (v < 0) {{ out[j++]='-'; mag=({utype})(-(v + ({ctype})1)); mag += ({utype})1; }} else mag=({utype})v;")
                o(f"    do {{ unsigned d=(unsigned)(mag % ({utype})10); tmp[i++]=(char)('0'+d); mag /= ({utype})10; }} while (mag != 0);")
                o("    while (i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
                o("}")
            else:
                o(f"AILANG_UNUSED static char *ailang_str_{suffix}({ctype} v) {{")
                o(f"    char *out=(char*)ailang_request_alloc({cap10}u); if (!out) return NULL;")
                o(f"    char tmp[{cap10}]; size_t i=0,j=0;")
                o(f"    do {{ unsigned d=(unsigned)(v % ({ctype})10); tmp[i++]=(char)('0'+d); v /= ({ctype})10; }} while (v != 0);")
                o("    while (i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
                o("}")

            # Base conversions use the unsigned bit-pattern, matching LLVM's
            # lshr-based fixed-width representation for negative signed values.
            o(f"AILANG_UNUSED static char *ailang_hex_{suffix}({ctype} input) {{")
            o(f"    {utype} v=({utype})input; static const char hd[] = \"0123456789ABCDEF\";")
            o(f"    char *out=(char*)ailang_request_alloc({bits // 4 + 4}u); if (!out) return NULL;")
            o(f"    char tmp[{bits // 4 + 1}]; size_t i=0,j=0;")
            o(f"    do {{ tmp[i++]=hd[(unsigned)(v & ({utype})15)]; v >>= 4; }} while (v != 0);")
            o("    out[j++]='0'; out[j++]='x'; while (i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            o("}")

            o(f"AILANG_UNUSED static char *ailang_bin_{suffix}({ctype} input) {{")
            o(f"    {utype} v=({utype})input;")
            o(f"    char *out=(char*)ailang_request_alloc({bits + 4}u); if (!out) return NULL;")
            o(f"    char tmp[{bits + 1}]; size_t i=0,j=0;")
            o(f"    do {{ tmp[i++]=(char)('0'+(unsigned)(v & ({utype})1)); v >>= 1; }} while (v != 0);")
            o("    out[j++]='0'; out[j++]='b'; while (i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            o("}")

            oct_cap = (bits + 2) // 3 + 4
            o(f"AILANG_UNUSED static char *ailang_oct_{suffix}({ctype} input) {{")
            o(f"    {utype} v=({utype})input;")
            o(f"    char *out=(char*)ailang_request_alloc({oct_cap}u); if (!out) return NULL;")
            o(f"    char tmp[{oct_cap}]; size_t i=0,j=0;")
            o(f"    do {{ tmp[i++]=(char)('0'+(unsigned)(v & ({utype})7)); v >>= 3; }} while (v != 0);")
            o("    out[j++]='0'; out[j++]='o'; while (i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            o("}")
            o(f"AILANG_UNUSED static void ailang_write_hex_{suffix}(FILE *f, {ctype} v) {{ char *s=ailang_hex_{suffix}(v); if (s) {{ fputs(s,f); ailang_safe_free(s); }} }}")
            o(f"AILANG_UNUSED static void ailang_write_bin_{suffix}(FILE *f, {ctype} v) {{ char *s=ailang_bin_{suffix}(v); if (s) {{ fputs(s,f); ailang_safe_free(s); }} }}")
            o(f"AILANG_UNUSED static void ailang_write_oct_{suffix}(FILE *f, {ctype} v) {{ char *s=ailang_oct_{suffix}(v); if (s) {{ fputs(s,f); ailang_safe_free(s); }} }}")
    o("#endif")
    o("#undef AILANG_WIDE_SAFE_BINOP_UNSIGNED")
    o("#undef AILANG_WIDE_SAFE_BINOP_SIGNED")
    o("#undef AILANG_WIDE_DIV_UNSIGNED")
    o("#undef AILANG_WIDE_DIV_SIGNED")
    o("#undef AILANG_WIDE_SHIFT")
    o("#undef AILANG_WIDE_POW")
    o("#ifndef AILANG_FREESTANDING")
    o("#undef AILANG_WIDE_WRITE_UNSIGNED")
    o("#undef AILANG_WIDE_WRITE_SIGNED")
    o("#endif")
    o("")
