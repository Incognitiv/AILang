"""Runtime emitter helpers for runtime emit math."""

from __future__ import annotations

__all__ = ["emit_runtime_math"]


def emit_runtime_math(self) -> None:
    """Emit math-related runtime helpers."""
    # Safe division (exits on division by zero AND INT_MIN/-1 overflow)
    if "safe_div" in self._needs.helpers:
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_div(int64_t a, int64_t b) {"
        )
        self._output.append("    if (b == 0) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append('        fprintf(stderr, "Error: division by zero\\n");')
        self._output.append("#endif")
        self._output.append('        __ailang_safety_trap("division by zero");')
        self._output.append("    }")
        self._output.append("    /* Check INT_MIN / -1 overflow (UB in C) */")
        self._output.append("    if (a == INT64_MIN && b == -1) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: integer overflow (INT_MIN / -1)\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("integer overflow (INT_MIN / -1)");'
        )
        self._output.append("    }")
        self._output.append("    return a / b;")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_mod(int64_t a, int64_t b) {"
        )
        self._output.append("    if (b == 0) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append('        fprintf(stderr, "Error: modulo by zero\\n");')
        self._output.append("#endif")
        self._output.append('        __ailang_safety_trap("modulo by zero");')
        self._output.append("    }")
        self._output.append("    /* Check INT_MIN % -1 overflow (UB in C) */")
        self._output.append("    if (a == INT64_MIN && b == -1) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: integer overflow (INT_MIN %% -1)\\n");'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("integer overflow (INT_MIN % -1)");'
        )
        self._output.append("    }")
        self._output.append("    return a % b;")
        self._output.append("}")
        self._output.append("")
        # Safe float division (catches 0.0 / 0.0)
        self._output.append(
            "AILANG_UNUSED static double ailang_safe_fdiv(double a, double b) {"
        )
        self._output.append("    if (b == 0.0) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: float division by zero\\n");'
        )
        self._output.append("#endif")
        self._output.append('        __ailang_safety_trap("float division by zero");')
        self._output.append("    }")
        self._output.append("    return a / b;")
        self._output.append("}")
        self._output.append("")
        # Safe shift operations (bounds check shift amount)
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_shl(int64_t a, int64_t shift) {"
        )
        self._output.append("    if (shift < 0 || shift >= 64) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: shift amount %lld out of '
            'bounds [0, 64)\\n", (long long)shift);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("shift amount out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return a << shift;")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_safe_shr(int64_t a, int64_t shift) {"
        )
        self._output.append("    if (shift < 0 || shift >= 64) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: shift amount %lld out of '
            'bounds [0, 64)\\n", (long long)shift);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("shift amount out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return a >> shift;")
        self._output.append("}")
        self._output.append("")
    # Emit safe shift helpers if shifts are used (separate from safe_div)
    if "safe_shift" in self._needs.helpers and ("safe_div" not in self._needs.helpers):
        self._output.append(
            "static int64_t ailang_safe_shl(int64_t a, int64_t shift) {"
        )
        self._output.append("    if (shift < 0 || shift >= 64) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: shift amount %lld out of '
            'bounds [0, 64)\\n", (long long)shift);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("shift amount out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return a << shift;")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "static int64_t ailang_safe_shr(int64_t a, int64_t shift) {"
        )
        self._output.append("    if (shift < 0 || shift >= 64) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: shift amount %lld out of '
            'bounds [0, 64)\\n", (long long)shift);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("shift amount out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return a >> shift;")
        self._output.append("}")
        self._output.append("")
    # Absolute value (only if user hasn't defined their own abs)
    if "abs" in self._needs.helpers and "abs" not in self._user_defined_funcs:
        self._output.append("static int64_t ailang_rt_abs(int64_t x) {")
        self._output.append("    return x < 0 ? -x : x;")
        self._output.append("}")
        self._output.append("")


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = emit_runtime_math
