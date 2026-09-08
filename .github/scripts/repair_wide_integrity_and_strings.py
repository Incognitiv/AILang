from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"repair anchor missing in {path}: {old[:120]!r}")
    if text.count(old) != 1:
        raise SystemExit(f"repair anchor not unique in {path}: count={text.count(old)}")
    write(path, text.replace(old, new, 1))


def add_after(path: str, anchor: str, addition: str) -> None:
    text = read(path)
    if addition.strip() in text:
        return
    if anchor not in text:
        raise SystemExit(f"add anchor missing in {path}: {anchor[:120]!r}")
    write(path, text.replace(anchor, anchor + addition, 1))


# ---------------------------------------------------------------------------
# 1. Mistus-style representation honesty: unknown types never become i64.
# ---------------------------------------------------------------------------
replace_once(
    "source/codegen/type_lowering.py",
    '''        # Unknown type - warn and fall back to i64 for compatibility.\n        # This should ideally be a hard error, but some stdlib C function\n        # declarations rely on the fallback.  Emit a warning so callers\n        # notice the silent coercion.\n\n        warnings.warn(\n            f"get_llvm_type: unknown type '{type_spec}', defaulting to i64",\n            stacklevel=2,\n        )\n        return ir.IntType(64)\n''',
    '''        # Mistus-style executable-boundary rule: catalog/syntax recognition\n        # is not permission to fabricate a representation.  Falling back to\n        # i64 made type-collector bugs silently erase i128..i8192 semantics.\n        raise CodeGenError(\n            f"LLVM backend: no executable representation for AILang type {type_spec!r}"\n        )\n''',
)
# warnings was only needed by the removed fallback.
text = read("source/codegen/type_lowering.py")
text = text.replace("import warnings\n", "")
write("source/codegen/type_lowering.py", text)

replace_once(
    "source/transpiler/core_type_lowering.py",
    '''        if spec.startswith("[") and spec.endswith("]"):\n            inner = spec[1:-1]\n            return f"{self._ailang_type_to_c(inner)} *"\n        return "int64_t"\n''',
    '''        if spec.startswith("[") and spec.endswith("]"):\n            inner = spec[1:-1]\n            return f"{self._ailang_type_to_c(inner)} *"\n        # Never manufacture an i64 representation for an unknown type.\n        # If a C ABI spelling is legitimate it must be mapped explicitly.\n        raise ValueError(\n            f"C backend: no executable representation for AILang type {spec!r}"\n        )\n''',
)

# ---------------------------------------------------------------------------
# 2. FreeBSD num_cpus(): stop using Linux's numeric sysconf constant 84.
# ---------------------------------------------------------------------------
replace_once(
    "source/transpiler/expr_threading_core.py",
    '''    # POSIX: Use sysconf(_SC_NPROCESSORS_ONLN) - value 84 on Linux\n    sysconf_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(32)])\n    sysconf = ir.Function(self.codegen.module, sysconf_ty, "sysconf")\n    sc_nprocessors = ir.Constant(ir.IntType(32), 84)\n    result = self.builder.call(sysconf, [sc_nprocessors], name="cpu_count")\n    # sysconf returns -1 on error, clamp to minimum of 1\n    one = ir.Constant(ir.IntType(64), 1)\n    is_valid = self.builder.icmp_signed(">", result, ir.Constant(ir.IntType(64), 0))\n    return self.builder.select(is_valid, result, one, name="cpu_count_safe")\n''',
    '''    triple = self.codegen.module.triple.lower()\n    if "freebsd" in triple:\n        # FreeBSD does not share Linux's numeric _SC_NPROCESSORS_ONLN value.\n        # Query the stable kernel sysctl instead of baking libc enum numbers\n        # into generated IR.  sysctlbyname is in libc on FreeBSD.\n        i8 = ir.IntType(8)\n        i8_ptr = i8.as_pointer()\n        i32 = ir.IntType(32)\n        i64 = ir.IntType(64)\n        sysctl_ty = ir.FunctionType(\n            i32, [i8_ptr, i8_ptr, i64.as_pointer(), i8_ptr, i64]\n        )\n        sysctl = self.codegen.module.globals.get("sysctlbyname")\n        if sysctl is None:\n            sysctl = ir.Function(self.codegen.module, sysctl_ty, "sysctlbyname")\n        name = self.codegen.create_string_constant("hw.ncpu")\n        count_ptr = self.builder.alloca(i32, name="freebsd_ncpu")\n        size_ptr = self.builder.alloca(i64, name="freebsd_ncpu_size")\n        self.builder.store(ir.Constant(i64, 4), size_ptr)\n        oldp = self.builder.bitcast(count_ptr, i8_ptr, name="freebsd_ncpu_oldp")\n        nullp = ir.Constant(i8_ptr, None)\n        rc = self.builder.call(\n            sysctl,\n            [name, oldp, size_ptr, nullp, ir.Constant(i64, 0)],\n            name="freebsd_ncpu_rc",\n        )\n        count32 = self.builder.load(count_ptr, name="freebsd_ncpu_value")\n        ok_rc = self.builder.icmp_signed("==", rc, ir.Constant(i32, 0))\n        ok_count = self.builder.icmp_signed(">", count32, ir.Constant(i32, 0))\n        valid = self.builder.and_(ok_rc, ok_count, name="freebsd_ncpu_valid")\n        count64 = self.builder.zext(count32, i64, name="freebsd_ncpu_i64")\n        return self.builder.select(\n            valid, count64, ir.Constant(i64, 1), name="cpu_count_safe"\n        )\n\n    # Linux/POSIX fallback.  Linux defines _SC_NPROCESSORS_ONLN as 84.\n    # Other targets should gain an explicit target implementation instead of\n    # silently reusing this numeric constant.\n    sysconf_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(32)])\n    sysconf = self.codegen.module.globals.get("sysconf")\n    if sysconf is None:\n        sysconf = ir.Function(self.codegen.module, sysconf_ty, "sysconf")\n    sc_nprocessors = ir.Constant(ir.IntType(32), 84)\n    result = self.builder.call(sysconf, [sc_nprocessors], name="cpu_count")\n    one = ir.Constant(ir.IntType(64), 1)\n    is_valid = self.builder.icmp_signed(">", result, ir.Constant(ir.IntType(64), 0))\n    return self.builder.select(is_valid, result, one, name="cpu_count_safe")\n''',
)

# ---------------------------------------------------------------------------
# 3. One-allocation JSON escaping. This is an edge adapter primitive, not a
#    JSON data model. It exists to keep unavoidable external JSON from turning
#    every byte into an owned temporary string.
# ---------------------------------------------------------------------------
llvm_escape = r'''
    def builtin_str_escape_json(self, args: list[ASTNode]) -> ir.Value:
        """Escape a string for a JSON string literal with one allocation.

        The old idiom `str_array_push(parts, chr(c)); str_array_join(...)`
        allocated one owned string per byte and leaked those borrowed array
        elements.  This routine allocates at most 2*n+1 bytes and writes the
        escaped bytes directly.
        """
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

        func = self.current_function
        header = func.append_basic_block("jsonesc_hdr")
        body = func.append_basic_block("jsonesc_body")
        escaped = func.append_basic_block("jsonesc_escaped")
        plain = func.append_basic_block("jsonesc_plain")
        merge = func.append_basic_block("jsonesc_merge")
        done = func.append_basic_block("jsonesc_done")
        self.current_builder.branch(header)

        self.current_builder.position_at_end(header)
        in_i = self.current_builder.phi(i64, name="jsonesc_i")
        out_i = self.current_builder.phi(i64, name="jsonesc_j")
        in_i.add_incoming(zero, self.current_builder.block)  # repaired below
        out_i.add_incoming(zero, self.current_builder.block)
        # The first incoming block must be the block that branched to header.
        preheader = header.predecessors[0] if hasattr(header, "predecessors") else None
        # llvmlite blocks do not expose predecessors reliably; replace the
        # placeholder incoming blocks from the branch source captured below.
'''
# We cannot use Block.predecessors portably in llvmlite. Build a simpler
# implementation with stack loop counters instead of PHIs.
llvm_escape = r'''
    def builtin_str_escape_json(self, args: list[ASTNode]) -> ir.Value:
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
        more = self.current_builder.icmp_unsigned("<", in_i, length, name="jsonesc_more")
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

'''
replace_once(
    "source/codegen/builtin_string.py",
    "    def builtin_str_replace(self, args: list[ASTNode]) -> ir.Value:\n",
    llvm_escape + "    def builtin_str_replace(self, args: list[ASTNode]) -> ir.Value:\n",
)

replace_once(
    "source/transpiler/expr_call_dispatch.py",
    '            "str_replace": lambda n: cg.builtin_str_replace(n),\n',
    '            "str_replace": lambda n: cg.builtin_str_replace(n),\n'
    '            "str_escape_json": lambda n: cg.builtin_str_escape_json(n),\n',
)
replace_once(
    "source/transpiler/expr_gen_call_builtin_map.py",
    '        "str_replace": lambda a: f"ailang_str_replace({a[0]}, {a[1]}, {a[2]})",\n',
    '        "str_replace": lambda a: f"ailang_str_replace({a[0]}, {a[1]}, {a[2]})",\n'
    '        "str_escape_json": lambda a: f"ailang_str_escape_json({a[0]})",\n',
)
replace_once(
    "source/transpiler/helper_scanner.py",
    '        "str_replace": "str_replace",\n',
    '        "str_replace": "str_replace",\n        "str_escape_json": "str_escape_json",\n',
)
replace_once(
    "source/transpiler/runtime_emitter.py",
    '                "str_replace",\n                "print",\n',
    '                "str_replace",\n                "str_escape_json",\n                "print",\n',
)

c_escape = r'''
    # JSON escaping: one allocation, no per-character temporary strings.
    if "str_escape_json" in self._needs.helpers:
        self._output.append("static char *ailang_str_escape_json(const char *s) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    if (!s) s = \"\";")
        self._output.append("    size_t n = strlen(s);")
        self._output.append("    char *out = (char *)ailang_request_alloc(n * 2u + 1u);")
        self._output.append("    if (!out) return NULL;")
        self._output.append("    char *p = out;")
        self._output.append("    for (size_t i = 0; i < n; i++) {")
        self._output.append("        unsigned char c = (unsigned char)s[i];")
        self._output.append("        switch (c) {")
        self._output.append("        case '\"': *p++ = '\\\\'; *p++ = '\"'; break;")
        self._output.append("        case '\\\\': *p++ = '\\\\'; *p++ = '\\\\'; break;")
        self._output.append("        case '\\n': *p++ = '\\\\'; *p++ = 'n'; break;")
        self._output.append("        case '\\r': *p++ = '\\\\'; *p++ = 'r'; break;")
        self._output.append("        case '\\t': *p++ = '\\\\'; *p++ = 't'; break;")
        self._output.append("        case '\\b': *p++ = '\\\\'; *p++ = 'b'; break;")
        self._output.append("        default: *p++ = (char)c; break;")
        self._output.append("        }")
        self._output.append("    }")
        self._output.append("    *p = '\\0';")
        self._output.append("    return out;")
        self._output.append("#else")
        self._output.append("    (void)s; return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

'''
replace_once(
    "source/transpiler/runtime_emit_string.py",
    "    # String replace\n",
    c_escape + "    # String replace\n",
)

# Return-type and ownership metadata must agree in every analysis phase.
for path in [
    "source/transpiler/type_info.py",
    "source/transpiler/core.py",
    "source/transpiler/type_collector.py",
    "source/transpiler/drop_plan.py",
    "source/diagnostics/static_analysis_perf.py",
]:
    text = read(path)
    if '"str_escape_json"' not in text:
        # Insert after the first str_replace entry in the relevant string set.
        marker = '            "str_replace",\n'
        if marker in text:
            text = text.replace(marker, marker + '            "str_escape_json",\n', 1)
        else:
            marker = '        "str_replace",\n'
            if marker not in text:
                raise SystemExit(f"cannot add str_escape_json metadata to {path}")
            text = text.replace(marker, marker + '        "str_escape_json",\n', 1)
        write(path, text)

# CTranspiler has two separate sets: returning and owning. Ensure both contain it.
text = read("source/transpiler/core.py")
return_anchor = '            "read_stdin",\n        }\n    )\n    # Builtins that always allocate a fresh heap string.'
if '"str_escape_json"' not in text.split("# Builtins that always allocate", 1)[0]:
    text = text.replace(
        return_anchor,
        '            "read_stdin",\n            "str_escape_json",\n        }\n    )\n    # Builtins that always allocate a fresh heap string.',
        1,
    )
# owning section insertion might already have happened above; guarantee it.
owning_start = text.index("_STRING_OWNING_CALLS")
owning_end = text.index("_NON_CAPTURING_CALLS", owning_start)
owning = text[owning_start:owning_end]
if '"str_escape_json"' not in owning:
    owning = owning.replace('            "str_replace",\n', '            "str_replace",\n            "str_escape_json",\n', 1)
    text = text[:owning_start] + owning + text[owning_end:]
write("source/transpiler/core.py", text)

# TypeCollector owns a separate set used to infer ownership across user functions.
text = read("source/transpiler/type_collector.py")
idx = text.find("_OWNING_STRING_CALLS")
if idx >= 0:
    end = text.find("    def _identify_owned_string_returns", idx)
    chunk = text[idx:end]
    if '"str_escape_json"' not in chunk:
        chunk = chunk.replace('            "str_replace",\n', '            "str_replace",\n            "str_escape_json",\n', 1)
        text = text[:idx] + chunk + text[end:]
        write("source/transpiler/type_collector.py", text)

# Drop-plan's compact owned-call set has different indentation.
text = read("source/transpiler/drop_plan.py")
needle = '        "str_replace",\n    }:\n'
if '"str_escape_json"' not in text[text.find("_default_owned_string_alloc"):text.find("def _call_name")]:
    if needle not in text:
        raise SystemExit("drop_plan owned-string anchor missing")
    text = text.replace(needle, '        "str_replace",\n        "str_escape_json",\n    }:\n', 1)
    write("source/transpiler/drop_plan.py", text)

# ---------------------------------------------------------------------------
# 4. Static ownership diagnostic: borrowed str_array + owned element = leak.
# ---------------------------------------------------------------------------
perf_path = "source/diagnostics/static_analysis_perf.py"
perf = read(perf_path)
if "check_owned_string_into_borrowed_str_array" not in perf:
    perf += r'''

_OWNED_STRING_PRODUCERS: frozenset[str] = frozenset(
    {
        "str",
        "chr",
        "substr",
        "concat",
        "str_replace",
        "str_escape_json",
        "hex",
        "bin",
        "oct",
        "read_file",
        "read_stdin",
        "input",
        "process_capture",
        "tcp_recv",
    }
)


def _owned_string_expr(node: A.ASTNode) -> bool:
    if isinstance(node, (A.InterpolatedString, A.StringSlice)):
        return True
    if isinstance(node, A.Call):
        return node.name in _OWNED_STRING_PRODUCERS
    if isinstance(node, A.BinaryOp) and node.op in {"+", "plus"}:
        return _looks_like_string(node)
    return False


def check_owned_string_into_borrowed_str_array(
    analyzer: _WarnCollector, node: A.ASTNode
) -> None:
    """Flag ownership transfer that str_array cannot perform.

    str_array stores borrowed string pointers and dealloc_str_array intentionally
    frees only the container.  Pushing a fresh owned string directly therefore
    loses the only ownership handle and leaks it.
    """
    if node is None:
        return
    if isinstance(node, A.Call) and node.name == "str_array_push" and len(node.args) >= 2:
        value = node.args[1]
        if _owned_string_expr(value):
            analyzer.warnings.append(
                AnalysisWarning(
                    line=getattr(node, "line", 0),
                    column=getattr(node, "column", 0),
                    category="memory",
                    message=(
                        "OWNERSHIP LEAK: str_array stores borrowed string pointers, "
                        "but this push receives a freshly allocated string. The "
                        "container will not free that element."
                    ),
                    suggestion=(
                        "Use a one-allocation transformation/builder (for JSON escaping, "
                        "str_escape_json), or retain and explicitly release each owned "
                        "element after the array is no longer used."
                    ),
                    severity="warning",
                )
            )
    values = vars(node).values() if hasattr(node, "__dict__") else ()
    for value in values:
        if isinstance(value, A.ASTNode):
            check_owned_string_into_borrowed_str_array(analyzer, value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, A.ASTNode):
                    check_owned_string_into_borrowed_str_array(analyzer, item)
'''
    write(perf_path, perf)

replace_once(
    "source/diagnostics/static_analysis.py",
    "from diagnostics.static_analysis_perf import check_string_concat_loops\n",
    "from diagnostics.static_analysis_perf import (\n"
    "    check_owned_string_into_borrowed_str_array,\n"
    "    check_string_concat_loops,\n"
    ")\n",
)
replace_once(
    "source/diagnostics/static_analysis.py",
    '''        for node in ast_nodes:\n            check_string_concat_loops(self, node, in_loop=False)\n\n        return self.warnings\n''',
    '''        for node in ast_nodes:\n            check_string_concat_loops(self, node, in_loop=False)\n            check_owned_string_into_borrowed_str_array(self, node)\n\n        return self.warnings\n''',
)

# The old suggestion is not ownership-safe for allocated per-element strings.
text = read(perf_path)
text = text.replace(
    '"Build with str_array_push into a str_array_new(cap), "\n'
    '                            \'then str_array_join(arr, "") once at the end. O(n) \'\n'
    '                            "total, single allocation."',
    '"Use concat()/a dedicated builder or an arena-scoped buffer. If "\n'
    '                            "using str_array, push only borrowed strings; owned "\n'
    '                            "temporaries require explicit lifetime management."',
)
write(perf_path, text)

# ---------------------------------------------------------------------------
# 5. Persistent regression tests: fixed-wide representation and strict fallback.
# ---------------------------------------------------------------------------
test_path = ROOT / "tests/test_fixed_wide_representation_integrity.py"
test_path.write_text(
r'''from __future__ import annotations

from types import SimpleNamespace

import pytest
from llvmlite import ir

from codegen.codegen import CodeGenError
from codegen.type_lowering import TypeLowering
from transpiler.core import CTranspiler


def _dummy_codegen():
    return SimpleNamespace(
        type_aliases={},
        class_types={},
        data_enum_types={},
        opaque_record_names=set(),
        record_types={},
        classes={},
        get_dict_type=lambda: ir.LiteralStructType([]),
        _get_bigint_type=lambda: ir.IntType(8).as_pointer(),
    )


@pytest.mark.parametrize("name,width", [
    ("i128", 128),
    ("i256", 256),
    ("i512", 512),
    ("i1024", 1024),
    ("i2048", 2048),
    ("i4096", 4096),
    ("i8192", 8192),
])
def test_llvm_fixed_wide_types_are_real_llvm_integers(name, width):
    ty = TypeLowering(_dummy_codegen()).get_llvm_type(name)
    assert isinstance(ty, ir.IntType)
    assert ty.width == width


@pytest.mark.parametrize("name,ctype", [
    ("i128", "__int128"),
    ("i256", "ailang_i256"),
    ("i512", "ailang_i512"),
    ("i1024", "ailang_i1024"),
    ("i2048", "ailang_i2048"),
    ("i4096", "ailang_i4096"),
    ("i8192", "ailang_i8192"),
])
def test_c_fixed_wide_types_are_not_i64(name, ctype):
    assert CTranspiler()._ailang_type_to_c(name) == ctype


def test_unknown_llvm_type_fails_closed():
    with pytest.raises(CodeGenError, match="no executable representation"):
        TypeLowering(_dummy_codegen()).get_llvm_type("DefinitelyNotAType")


def test_unknown_c_type_fails_closed():
    with pytest.raises(ValueError, match="no executable representation"):
        CTranspiler()._ailang_type_to_c("DefinitelyNotAType")
''',
encoding="utf-8",
)

print("repair applied")
