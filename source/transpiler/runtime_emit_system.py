"""Runtime emitter helpers for `system` runtime support."""

from __future__ import annotations

__all__ = ["emit_runtime_system"]

from typing import Any


def emit_runtime_system(emitter: Any) -> None:
    """Emit `system()` wrapper when requested."""
    if "system" not in emitter._needs.helpers:
        return

    emitter._output.append("/* system() command execution */")
    emitter._output.append("#ifndef AILANG_FREESTANDING")
    emitter._output.append("static int64_t ailang_system(const char *cmd) {")
    emitter._output.append("    return (int64_t)system(cmd);")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append(
        "AILANG_UNUSED static char *ailang_process_capture(const char *cmd) {"
    )
    emitter._output.append("    if (!cmd) {")
    emitter._output.append("        char *empty = (char *)ailang_safe_malloc(1);")
    emitter._output.append("        if (empty) empty[0] = '\\0';")
    emitter._output.append("        return empty;")
    emitter._output.append("    }")
    emitter._output.append("#if defined(_WIN32) || defined(_WIN64)")
    emitter._output.append('    FILE *pipe = _popen(cmd, "rb");')
    emitter._output.append("#else")
    emitter._output.append('    FILE *pipe = popen(cmd, "r");')
    emitter._output.append("#endif")
    emitter._output.append("    if (!pipe) {")
    emitter._output.append("        char *empty = (char *)ailang_safe_malloc(1);")
    emitter._output.append("        if (empty) empty[0] = '\\0';")
    emitter._output.append("        return empty;")
    emitter._output.append("    }")
    emitter._output.append("    size_t cap = 256;")
    emitter._output.append("    size_t len = 0;")
    emitter._output.append("    char *out = (char *)ailang_safe_malloc(cap);")
    emitter._output.append("    char chunk[256];")
    emitter._output.append("    if (!out) {")
    emitter._output.append("#if defined(_WIN32) || defined(_WIN64)")
    emitter._output.append("        _pclose(pipe);")
    emitter._output.append("#else")
    emitter._output.append("        pclose(pipe);")
    emitter._output.append("#endif")
    emitter._output.append("        return NULL;")
    emitter._output.append("    }")
    emitter._output.append("    out[0] = '\\0';")
    emitter._output.append(
        "    while (fgets(chunk, (int)sizeof(chunk), pipe) != NULL) {"
    )
    emitter._output.append("        size_t got = strlen(chunk);")
    emitter._output.append("        if (got > AILANG_MAX_ALLOC_SIZE - len - 1) {")
    emitter._output.append("            ailang_safe_free(out);")
    emitter._output.append("#if defined(_WIN32) || defined(_WIN64)")
    emitter._output.append("            _pclose(pipe);")
    emitter._output.append("#else")
    emitter._output.append("            pclose(pipe);")
    emitter._output.append("#endif")
    emitter._output.append(
        '            __ailang_safety_trap("process_capture: output exceeds allocation cap");'
    )
    emitter._output.append("        }")
    emitter._output.append("        size_t need = len + got + 1;")
    emitter._output.append("        if (need > cap) {")
    emitter._output.append("            while (cap < need) cap *= 2;")
    emitter._output.append("            out = (char *)ailang_safe_realloc(out, cap);")
    emitter._output.append("        }")
    emitter._output.append("        memcpy(out + len, chunk, got + 1);")
    emitter._output.append("        len += got;")
    emitter._output.append("    }")
    emitter._output.append("#if defined(_WIN32) || defined(_WIN64)")
    emitter._output.append("    _pclose(pipe);")
    emitter._output.append("#else")
    emitter._output.append("    pclose(pipe);")
    emitter._output.append("#endif")
    emitter._output.append("    return out;")
    emitter._output.append("}")
    emitter._output.append("#endif")
    emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is only
# imported through import wiring from ``runtime_emitter``.
_exported_emit_runtime_system = emit_runtime_system
