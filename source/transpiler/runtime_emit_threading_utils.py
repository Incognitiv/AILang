"""Runtime emitter helpers for threading utility builtins."""

from __future__ import annotations

__all__ = ["emit_runtime_threading_utils"]

from typing import Any


def emit_runtime_threading_utils(emitter: Any) -> None:
    """Emit helper threading runtime functions:
    thread_id / num_cpus / yield_thread / sleep_ms.
    """
    if "threading_utils" not in emitter._needs.helpers:
        return

    emitter._output.append("/* Threading utility builtins */")
    emitter._output.append("#ifndef AILANG_FREESTANDING")
    emitter._output.append("")
    # Need unistd.h for sysconf/_SC_NPROCESSORS_ONLN and sched.h for sched_yield
    emitter._output.append("#if !defined(AILANG_WINDOWS)")
    emitter._output.append("    #include <unistd.h>")
    emitter._output.append("    #include <sched.h>")
    emitter._output.append("#endif")
    emitter._output.append("")

    # thread_id
    emitter._output.append("static int64_t ailang_thread_id(void) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append("    return (int64_t)GetCurrentThreadId();")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    return (int64_t)thrd_current();")
    emitter._output.append("#else")
    emitter._output.append("    return (int64_t)pthread_self();")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    # num_cpus
    emitter._output.append("static int64_t ailang_num_cpus(void) {")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    SYSTEM_INFO si;")
    emitter._output.append("    GetSystemInfo(&si);")
    emitter._output.append("    return (int64_t)si.dwNumberOfProcessors;")
    emitter._output.append("#else")
    emitter._output.append("    return (int64_t)sysconf(_SC_NPROCESSORS_ONLN);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    # yield_thread
    emitter._output.append("AILANG_UNUSED static int64_t ailang_yield_thread(void) {")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    SwitchToThread();")
    emitter._output.append("#else")
    emitter._output.append("    sched_yield();")
    emitter._output.append("#endif")
    emitter._output.append("    return 0;")
    emitter._output.append("}")
    emitter._output.append("")

    # sleep_ms
    emitter._output.append("AILANG_UNUSED static int64_t ailang_sleep_ms(int64_t ms) {")
    emitter._output.append("#ifdef AILANG_WINDOWS")
    emitter._output.append("    Sleep((DWORD)ms);")
    emitter._output.append("#else")
    emitter._output.append("    struct timespec ts;")
    emitter._output.append("    ts.tv_sec = ms / 1000;")
    emitter._output.append("    ts.tv_nsec = (ms % 1000) * 1000000;")
    emitter._output.append("    nanosleep(&ts, NULL);")
    emitter._output.append("#endif")
    emitter._output.append("    return 0;")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("#endif /* !AILANG_FREESTANDING */")
    emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is
# only imported through import wiring from ``runtime_emitter``.
_exported_emit_runtime_threading_utils = emit_runtime_threading_utils
