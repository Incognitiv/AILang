"""Runtime emitter helpers for time-related C helpers."""

from __future__ import annotations

__all__ = ["emit_runtime_time"]

from typing import Any


def emit_runtime_time(emitter: Any) -> None:
    """Append time helper C functions based on requested runtime needs."""
    # Time functions - universal implementation
    if "time_ns" in emitter._needs.helpers:
        emitter._output.append("/* High-resolution timer - cross-platform */")
        emitter._output.append("#ifdef AILANG_WINDOWS")
        emitter._output.append("static int64_t time_ns(void) {")
        emitter._output.append("    LARGE_INTEGER freq, counter;")
        emitter._output.append("    QueryPerformanceFrequency(&freq);")
        emitter._output.append("    QueryPerformanceCounter(&counter);")
        emitter._output.append(
            "    /* Decompose to avoid i64 overflow: naive counter*1e9 wraps"
        )
        emitter._output.append("       after ~15 min uptime when QPF is 10 MHz. */")
        emitter._output.append("    int64_t q = counter.QuadPart / freq.QuadPart;")
        emitter._output.append("    int64_t r = counter.QuadPart % freq.QuadPart;")
        emitter._output.append(
            "    return q * 1000000000LL + (r * 1000000000LL) / freq.QuadPart;"
        )
        emitter._output.append("}")
        emitter._output.append(
            "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
        )
        emitter._output.append("static int64_t time_ns(void) {")
        emitter._output.append("    struct timespec ts;")
        emitter._output.append("    clock_gettime(CLOCK_MONOTONIC, &ts);")
        emitter._output.append(
            "    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;"
        )
        emitter._output.append("}")
        emitter._output.append("#elif defined(AILANG_MACOS)")
        emitter._output.append(
            "/* macOS has clock_gettime since 10.12, but use mach for compatibility */"
        )
        emitter._output.append("#include <mach/mach_time.h>")
        emitter._output.append("static int64_t time_ns(void) {")
        emitter._output.append("    static mach_timebase_info_data_t timebase;")
        emitter._output.append("    if (timebase.denom == 0) {")
        emitter._output.append("        mach_timebase_info(&timebase);")
        emitter._output.append("    }")
        emitter._output.append("    uint64_t abs_time = mach_absolute_time();")
        emitter._output.append(
            "    return (int64_t)((abs_time * timebase.numer) / timebase.denom);"
        )
        emitter._output.append("}")
        emitter._output.append("#elif defined(AILANG_FREESTANDING)")
        emitter._output.append("/* Freestanding: return 0 (no system timer) */")
        emitter._output.append("static int64_t time_ns(void) {")
        emitter._output.append("    return 0LL;")
        emitter._output.append("}")
        emitter._output.append("#else")
        emitter._output.append("/* Fallback for unknown platforms */")
        emitter._output.append("static int64_t time_ns(void) {")
        emitter._output.append("    return 0LL;")
        emitter._output.append("}")
        emitter._output.append("#endif")
        emitter._output.append("")

        emitter._output.append("static int64_t clock_ns(void) { return time_ns(); }")
        emitter._output.append("")

    # time_ms helper (milliseconds)
    if "time_ms" in emitter._needs.helpers:
        emitter._output.append("static int64_t time_ms(void) {")
        emitter._output.append("#if defined(AILANG_WINDOWS)")
        emitter._output.append("    LARGE_INTEGER freq, count;")
        emitter._output.append("    QueryPerformanceFrequency(&freq);")
        emitter._output.append("    QueryPerformanceCounter(&count);")
        emitter._output.append("    return (count.QuadPart * 1000LL) / freq.QuadPart;")
        emitter._output.append("#elif defined(AILANG_MACOS)")
        emitter._output.append(
            "    return (int64_t)(mach_absolute_time() / 1000000ULL);"
        )
        emitter._output.append(
            "#elif defined(AILANG_LINUX) || defined(AILANG_BSD) || defined(AILANG_UNIX)"
        )
        emitter._output.append("    struct timespec ts;")
        emitter._output.append("    clock_gettime(CLOCK_MONOTONIC, &ts);")
        emitter._output.append(
            "    return (int64_t)ts.tv_sec * 1000LL + (int64_t)ts.tv_nsec / 1000000LL;"
        )
        emitter._output.append("#else")
        emitter._output.append("    return 0LL;")
        emitter._output.append("#endif")
        emitter._output.append("}")
        emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is only
# consumed through import wiring from ``runtime_emitter``.
_exported_emit_runtime_time = emit_runtime_time
