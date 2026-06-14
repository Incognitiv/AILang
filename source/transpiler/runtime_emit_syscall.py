"""C runtime emitter for native syscall builtins."""

from __future__ import annotations

from typing import Any


def emit_runtime_syscall(emitter: Any) -> None:
    """Emit the target-adapted syscall helper family.

    The public AILang surface is intentionally unified (`syscall(n, ...)`).
    The generated helper compiles on every C target. Linux gets the native
    `syscall` libc boundary; other targets return -ENOSYS so programs can
    branch explicitly instead of failing at build time.
    """
    o = emitter._output
    o.append("/* Native syscall runtime helpers */")
    o.append("#ifndef AILANG_ENOSYS")
    o.append("    #define AILANG_ENOSYS 38")
    o.append("#endif")
    o.append("#if defined(AILANG_LINUX)")
    o.append("    extern long syscall(long number, ...);")
    o.append("#endif")
    o.append(
        "AILANG_UNUSED static int64_t ailang_syscall_native("
        "int64_t number, int64_t a0, int64_t a1, int64_t a2, "
        "int64_t a3, int64_t a4, int64_t a5) {"
    )
    o.append("#if defined(AILANG_LINUX)")
    o.append(
        "    return (int64_t)syscall((long)number, (long)a0, (long)a1, "
        "(long)a2, (long)a3, (long)a4, (long)a5);"
    )
    o.append("#else")
    o.append("    (void)number; (void)a0; (void)a1; (void)a2;")
    o.append("    (void)a3; (void)a4; (void)a5;")
    o.append("    return -(int64_t)AILANG_ENOSYS;")
    o.append("#endif")
    o.append("}")
    o.append("")
