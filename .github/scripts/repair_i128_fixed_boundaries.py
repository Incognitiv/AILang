from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read(path):
    return (ROOT / path).read_text(encoding="utf-8")

def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")

def replace_once(path, old, new):
    text = read(path)
    if text.count(old) != 1:
        raise SystemExit(f"anchor mismatch {path}: {text.count(old)} for {old[:100]!r}")
    write(path, text.replace(old, new, 1))

# Complete fixed-width metadata, not the legacy 256+ subset, owns >64-bit boundaries.
replace_once(
    "source/transpiler/expr_gen_call_impl.py",
    "from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr\n",
    "from transpiler.fixed_int_cast_codegen import checked_fixed_int_conversion_expr\nfrom transpiler.fixed_int_types import info_for_c_fixed\n",
)
replace_once(
    "source/transpiler/expr_gen_call_impl.py",
    '''def _runtime_i64_arg(self, node: A.ASTNode, expr: str) -> str:\n    \"\"\"Narrow a language integer to an i64 runtime boundary without data loss.\"\"\"\n    wide = info_for_c(self._infer_type(node))\n    if wide is not None:\n        return f\"ailang_narrow_i64_{wide.suffix}({expr})\"\n    return expr\n''',
    '''def _runtime_i64_arg(self, node: A.ASTNode, expr: str) -> str:\n    \"\"\"Narrow a language integer to an i64 runtime boundary without data loss.\"\"\"\n    fixed = info_for_c_fixed(self._infer_type(node))\n    if fixed is not None and fixed.bits > 64:\n        return f\"ailang_narrow_i64_{fixed.canonical}({expr})\"\n    return expr\n''',
)
replace_once(
    "source/transpiler/expr_gen_call_impl.py",
    '''        wide = info_for_c(self._infer_type(arg_node))\n        if wide is not None:\n            return f\"ailang_{node.name}_{wide.suffix}({arg_expr})\"\n''',
    '''        fixed = info_for_c_fixed(self._infer_type(arg_node))\n        if fixed is not None and fixed.bits > 64:\n            return f\"ailang_{node.name}_{fixed.canonical}({arg_expr})\"\n''',
)
replace_once(
    "source/transpiler/expr_gen_call_impl.py",
    '''        if info_for_c(self._infer_type(idx_node)) is not None:\n            arr = self.expr(arg_at(node, 0))\n''',
    '''        idx_fixed = info_for_c_fixed(self._infer_type(idx_node))\n        if idx_fixed is not None and idx_fixed.bits > 64:\n            arr = self.expr(arg_at(node, 0))\n''',
)

# In-place dynamic array push must enforce the same i64 boundary for i128/u128.
replace_once(
    "source/transpiler/stmt_visit_data.py",
    '''            wide = info_for_c(self._infer_type(value_node))\n            if wide is not None:\n                value = f\"ailang_narrow_i64_{wide.suffix}({value})\"\n''',
    '''            fixed = info_for_c_fixed(self._infer_type(value_node))\n            if fixed is not None and fixed.bits > 64:\n                value = f\"ailang_narrow_i64_{fixed.canonical}({value})\"\n''',
)

# Materialized interpolation must preserve >64-bit fixed values.
replace_once(
    "source/transpiler/expr_gen_basic_impl.py",
    "from typing import Any, List\n",
    "from typing import Any, List\n\nfrom transpiler.fixed_int_types import info_for_c_fixed\n",
)
replace_once(
    "source/transpiler/expr_gen_basic_impl.py",
    '''            else:\n                parts_code.append(f\"ailang_int_to_str({expr_val})\")\n                parts_owned.append(True)\n''',
    '''            else:\n                fixed = info_for_c_fixed(self._infer_type(part))\n                if fixed is not None and fixed.bits > 64:\n                    parts_code.append(f\"ailang_str_{fixed.canonical}({expr_val})\")\n                else:\n                    parts_code.append(f\"ailang_int_to_str({expr_val})\")\n                parts_owned.append(True)\n''',
)

# Direct-print interpolation/base conversion uses typed i128/u128 writers too.
replace_once(
    "source/transpiler/stmt_visit_calls.py",
    '''        if inferred in unsigned_int_types:\n            chunks.append({\"kind\": \"u64\", \"expr\": self.expr(part)})\n            continue\n        if inferred in signed_int_types:\n            chunks.append({\"kind\": \"i64\", \"expr\": self.expr(part)})\n            continue\n''',
    '''        fixed = info_for_c_fixed(inferred)\n        if fixed is not None and fixed.bits > 64:\n            chunks.append({\"kind\": \"fixed\", \"expr\": self.expr(part), \"suffix\": fixed.canonical})\n            continue\n        if inferred in unsigned_int_types:\n            chunks.append({\"kind\": \"u64\", \"expr\": self.expr(part)})\n            continue\n        if inferred in signed_int_types:\n            chunks.append({\"kind\": \"i64\", \"expr\": self.expr(part)})\n            continue\n''',
)
replace_once(
    "source/transpiler/stmt_visit_calls.py",
    '''        if kind == \"u64\":\n            self.emit(f\"        ailang_write_u64(stdout, (uint64_t)({expr}));\")\n            continue\n        self.emit(f\"        ailang_write_i64(stdout, (int64_t)({expr}));\")\n''',
    '''        if kind == \"u64\":\n            self.emit(f\"        ailang_write_u64(stdout, (uint64_t)({expr}));\")\n            continue\n        if kind == \"fixed\":\n            suffix = str(chunk.get(\"suffix\", \"i128\"))\n            self.emit(f\"        ailang_write_{suffix}(stdout, {expr});\")\n            continue\n        self.emit(f\"        ailang_write_i64(stdout, (int64_t)({expr}));\")\n''',
)
replace_once(
    "source/transpiler/stmt_visit_calls.py",
    '''                wide = info_for_c(self._infer_type(inner_node))\n                if wide is not None:\n                    self.emit(\n                        f\"        ailang_write_{base_kind}_{wide.suffix}(stdout, {inner_expr});\"\n                    )\n''',
    '''                fixed = info_for_c_fixed(self._infer_type(inner_node))\n                if fixed is not None and fixed.bits > 64:\n                    self.emit(\n                        f\"        ailang_write_{base_kind}_{fixed.canonical}(stdout, {inner_expr});\"\n                    )\n''',
)

# i128 narrowing is an always-available fixed-int helper, not a 256+ wide runtime helper.
replace_once(
    "source/transpiler/runtime_emit_fixed_int_casts.py",
    '''    _emit_family(self, 128)\n    _emit_arithmetic_family(self, 128)\n    o(\"#endif\")\n''',
    '''    _emit_family(self, 128)\n    _emit_arithmetic_family(self, 128)\n    o('AILANG_UNUSED static int64_t ailang_narrow_i64_i128(__int128 v) { if (v < (__int128)INT64_MIN || v > (__int128)INT64_MAX) __ailang_safety_trap(\"integer value does not fit signed 64-bit boundary\"); return (int64_t)v; }')\n    o('AILANG_UNUSED static int64_t ailang_narrow_i64_u128(unsigned __int128 v) { if (v > (unsigned __int128)INT64_MAX) __ailang_safety_trap(\"integer value does not fit signed 64-bit boundary\"); return (int64_t)v; }')\n    o(\"#endif\")\n''',
)

# Materializing i128/u128 decimal/base converters share the normal string allocator.
anchor = '''        self._output.append(\"\")\n\n    # Typed stdout writers (P11): avoid generic printf format parsing in\n'''
helpers = r'''        self._output.append("")

    if self._needs.helpers.intersection({"int_to_str", "base_conv"}):
        self._output.append("#if defined(__SIZEOF_INT128__)")
        self._output.append("AILANG_UNUSED static char *ailang_str_u128(unsigned __int128 v) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    char *out=(char*)ailang_request_alloc(48); if (!out) return NULL; char tmp[48]; size_t i=0,j=0;")
        self._output.append("    do { unsigned d=(unsigned)(v % 10); tmp[i++]=(char)('0'+d); v/=10; } while (v!=0);")
        self._output.append("    while(i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
        self._output.append("#else")
        self._output.append("    (void)v; return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("AILANG_UNUSED static char *ailang_str_i128(__int128 v) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    unsigned __int128 mag; int neg=v<0; if(neg){ mag=(unsigned __int128)(-(v+1)); mag+=1; } else mag=(unsigned __int128)v;")
        self._output.append("    char *out=(char*)ailang_request_alloc(48); if (!out) return NULL; char tmp[48]; size_t i=0,j=0;")
        self._output.append("    do { unsigned d=(unsigned)(mag % 10); tmp[i++]=(char)('0'+d); mag/=10; } while (mag!=0);")
        self._output.append("    if(neg) out[j++]='-'; while(i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
        self._output.append("#else")
        self._output.append("    (void)v; return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        for prefix, ctype in (("i128", "__int128"), ("u128", "unsigned __int128")):
            self._output.append(f"AILANG_UNUSED static char *ailang_hex_{prefix}({ctype} input) {{")
            self._output.append("#ifndef AILANG_FREESTANDING")
            self._output.append("    unsigned __int128 v=(unsigned __int128)input; static const char hd[]=\"0123456789ABCDEF\"; char *out=(char*)ailang_request_alloc(35); if(!out) return NULL; char tmp[33]; size_t i=0,j=0;")
            self._output.append("    do { tmp[i++]=hd[(unsigned)(v & 15)]; v>>=4; } while(v); out[j++]='0'; out[j++]='x'; while(i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            self._output.append("#else")
            self._output.append("    (void)input; return NULL;")
            self._output.append("#endif")
            self._output.append("}")
            self._output.append(f"AILANG_UNUSED static char *ailang_bin_{prefix}({ctype} input) {{")
            self._output.append("#ifndef AILANG_FREESTANDING")
            self._output.append("    unsigned __int128 v=(unsigned __int128)input; char *out=(char*)ailang_request_alloc(131); if(!out) return NULL; char tmp[129]; size_t i=0,j=0;")
            self._output.append("    do { tmp[i++]=(char)('0'+(unsigned)(v&1)); v>>=1; } while(v); out[j++]='0'; out[j++]='b'; while(i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            self._output.append("#else")
            self._output.append("    (void)input; return NULL;")
            self._output.append("#endif")
            self._output.append("}")
            self._output.append(f"AILANG_UNUSED static char *ailang_oct_{prefix}({ctype} input) {{")
            self._output.append("#ifndef AILANG_FREESTANDING")
            self._output.append("    unsigned __int128 v=(unsigned __int128)input; char *out=(char*)ailang_request_alloc(47); if(!out) return NULL; char tmp[44]; size_t i=0,j=0;")
            self._output.append("    do { tmp[i++]=(char)('0'+(unsigned)(v&7)); v>>=3; } while(v); out[j++]='0'; out[j++]='o'; while(i) out[j++]=tmp[--i]; out[j]='\\0'; return out;")
            self._output.append("#else")
            self._output.append("    (void)input; return NULL;")
            self._output.append("#endif")
            self._output.append("}")
            self._output.append(f"AILANG_UNUSED static void ailang_write_hex_{prefix}(FILE *f, {ctype} v) {{ char *s=ailang_hex_{prefix}(v); if(s){{ fputs(s,f); ailang_safe_free(s); }} }}")
            self._output.append(f"AILANG_UNUSED static void ailang_write_bin_{prefix}(FILE *f, {ctype} v) {{ char *s=ailang_bin_{prefix}(v); if(s){{ fputs(s,f); ailang_safe_free(s); }} }}")
            self._output.append(f"AILANG_UNUSED static void ailang_write_oct_{prefix}(FILE *f, {ctype} v) {{ char *s=ailang_oct_{prefix}(v); if(s){{ fputs(s,f); ailang_safe_free(s); }} }}")
        self._output.append("#endif")
        self._output.append("")

    # Typed stdout writers (P11): avoid generic printf format parsing in
'''
replace_once("source/transpiler/runtime_emit_string.py", anchor, helpers)

# Persist regressions for the exact gap that caused LLVM/C divergence.
test_path = ROOT / "tests/test_fixed_wide_representation_integrity.py"
t = test_path.read_text(encoding="utf-8")
addition = r'''

def test_c_i128_formatting_and_i64_boundary_use_complete_fixed_metadata():
    call_impl = (ROOT / "source/transpiler/expr_gen_call_impl.py").read_text(encoding="utf-8")
    runtime_fixed = (ROOT / "source/transpiler/runtime_emit_fixed_int_casts.py").read_text(encoding="utf-8")
    runtime_string = (ROOT / "source/transpiler/runtime_emit_string.py").read_text(encoding="utf-8")
    assert "info_for_c_fixed(self._infer_type(arg_node))" in call_impl
    assert "ailang_narrow_i64_i128" in runtime_fixed
    assert "ailang_str_i128" in runtime_string
    assert "ailang_hex_i128" in runtime_string
    assert "ailang_bin_i128" in runtime_string
    assert "ailang_oct_i128" in runtime_string
'''
if "test_c_i128_formatting_and_i64_boundary_use_complete_fixed_metadata" not in t:
    test_path.write_text(t + addition, encoding="utf-8")

print("i128 boundary repair applied")
