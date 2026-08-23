"""Tail safety runtime emitters split from runtime_emit_safety."""

from __future__ import annotations

__all__ = ["emit_safety_tail_helpers"]


def emit_safety_tail_helpers(self) -> None:
    """Emit recursion, argv, array-bounds, and arithmetic safety helpers."""
    self._output.append("/* C23 Safety: Stack overflow protection */")
    self._output.append("static uint32_t __ailang_recursion_depth = 0U;")
    self._output.append("#define AILANG_MAX_RECURSION_DEPTH 10000U")
    self._output.append("#if defined(__GNUC__) || defined(__clang__)")
    self._output.append("#define AILANG_UNLIKELY(x) __builtin_expect(!!(x), 0)")
    self._output.append("#else")
    self._output.append("#define AILANG_UNLIKELY(x) (x)")
    self._output.append("#endif")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static void __ailang_recursion_overflow(const char *func_name) {"
    )
    self._output.append("    (void)func_name;")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        "        fprintf(stderr, \"Error: stack overflow in function '%s' "
        '(recursion depth > %u)\\n", func_name, AILANG_MAX_RECURSION_DEPTH);'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("stack overflow (recursion limit)");'
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "#define __ailang_check_recursion(func_name) "
        "do { "
        "if (AILANG_UNLIKELY(++__ailang_recursion_depth > "
        "AILANG_MAX_RECURSION_DEPTH)) { "
        "__ailang_recursion_overflow((func_name)); "
        "} "
        "} while (0)"
    )
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static inline void __ailang_end_recursion(void) {"
    )
    self._output.append("    --__ailang_recursion_depth;")
    self._output.append("}")
    self._output.append("")

    if "cmdline" in self._needs.helpers:
        self._output.append("/* Command-line argument access */")
        self._output.append("static int __ailang_argc = 0;")
        self._output.append("static char **__ailang_argv = nullptr;")
        self._output.append("")
        self._output.append("static int64_t ailang_argc(void) {")
        self._output.append("    return (int64_t)__ailang_argc;")
        self._output.append("}")
        self._output.append("")
        self._output.append("static const char *ailang_argv(int64_t idx) {")
        self._output.append("    if (idx < 0 || idx >= __ailang_argc) {")
        self._output.append('        return "";')
        self._output.append("    }")
        self._output.append("    return __ailang_argv[idx];")
        self._output.append("}")
        self._output.append("")
        self._output.append("static const char *ailang_getenv(const char *name) {")
        self._output.append("    const char *v = getenv(name);")
        self._output.append('    return v ? v : "";')
        self._output.append("}")
        self._output.append("")

    if "safe_array" in self._needs.helpers:
        self._output.append("/* C23 Safety: Bounds checking for array access */")
        self._output.append(
            "static int64_t ailang_safe_array_get(int64_t *arr, int64_t idx, int64_t len) {"
        )
        self._output.append("    if (idx < 0 || idx >= len) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: array index %lld out of bounds '
            '[0, %lld)\\n", (long long)idx, (long long)len);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("array index out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return arr[idx];")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "static void ailang_safe_array_set("
            "int64_t *arr, int64_t idx, int64_t len, int64_t val) {"
        )
        self._output.append("    if (idx < 0 || idx >= len) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: array index %lld out of bounds '
            '[0, %lld)\\n", (long long)idx, (long long)len);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("array index out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    arr[idx] = val;")
        self._output.append("}")
        self._output.append("")

    if "safe_add" in self._needs.helpers:
        self._output.append("/* C23 Safety: Integer overflow detection - addition */")
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_add(int64_t a, int64_t b) {"
        )
        self._output.append("#if defined(__GNUC__) || defined(__clang__)")
        self._output.append("    int64_t out;")
        self._output.append("    if (__builtin_add_overflow(a, b, &out)) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: integer overflow in addition\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("integer overflow in addition");'
        )
        self._output.append("    }")
        self._output.append("    return out;")
        self._output.append("#else")
        self._output.append("    if ((b > 0 && a > INT64_MAX - b) ||")
        self._output.append("        (b < 0 && a < INT64_MIN - b)) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: integer overflow in addition\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("integer overflow in addition");'
        )
        self._output.append("    }")
        self._output.append("    return a + b;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    if "safe_sub" in self._needs.helpers:
        self._output.append(
            "/* C23 Safety: Integer overflow detection - subtraction */"
        )
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_sub(int64_t a, int64_t b) {"
        )
        self._output.append("    if ((b < 0 && a > INT64_MAX + b) ||")
        self._output.append("        (b > 0 && a < INT64_MIN + b)) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: integer overflow in subtraction\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("integer overflow in subtraction");'
        )
        self._output.append("    }")
        self._output.append("    return a - b;")
        self._output.append("}")
        self._output.append("")

    if "safe_mul" in self._needs.helpers:
        self._output.append(
            "/* C23 Safety: Integer overflow detection - multiplication */"
        )
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_mul(int64_t a, int64_t b) {"
        )
        self._output.append("    if (a != 0 && b != 0) {")
        self._output.append("        if ((a > 0 && b > 0 && a > INT64_MAX / b) ||")
        self._output.append("            (a > 0 && b < 0 && b < INT64_MIN / a) ||")
        self._output.append("            (a < 0 && b > 0 && a < INT64_MIN / b) ||")
        self._output.append("            (a < 0 && b < 0 && a < INT64_MAX / b)) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '            fprintf(stderr, "Error: integer overflow in multiply\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '            __ailang_safety_trap("integer overflow in multiply");'
        )
        self._output.append("        }")
        self._output.append("    }")
        self._output.append("    return a * b;")
        self._output.append("}")
        self._output.append("")


_exported_runtime_emit_helpers = emit_safety_tail_helpers
