"""C runtime emitter for hosted status builtins."""

from __future__ import annotations

from typing import Any


def emit_runtime_status(emitter: Any) -> None:
    """Emit libc errno helpers.

    These are hosted-runtime helpers. They intentionally expose libc errno,
    not Win32 GetLastError; Windows-specific last-error remains available
    through the typed Win32 helper surface.
    """
    o = emitter._output
    o.append("/* Hosted status runtime helpers */")
    o.append("#ifndef AILANG_FREESTANDING")
    o.append("    #include <errno.h>")
    o.append("#endif")
    o.append("AILANG_UNUSED static int64_t ailang_errno_get(void) {")
    o.append("#ifndef AILANG_FREESTANDING")
    o.append("    return (int64_t)errno;")
    o.append("#else")
    o.append("    return 0;")
    o.append("#endif")
    o.append("}")
    o.append("AILANG_UNUSED static int64_t ailang_errno_clear(void) {")
    o.append("#ifndef AILANG_FREESTANDING")
    o.append("    errno = 0;")
    o.append("#endif")
    o.append("    return 0;")
    o.append("}")
    o.append("AILANG_UNUSED static int64_t ailang_errno_set(int64_t value) {")
    o.append("#ifndef AILANG_FREESTANDING")
    o.append("    errno = (int)value;")
    o.append("#endif")
    o.append("    return value;")
    o.append("}")
    o.append("")
