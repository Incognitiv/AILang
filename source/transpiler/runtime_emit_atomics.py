"""Runtime emitter helpers for atomic helpers."""

from __future__ import annotations

__all__ = ["emit_runtime_atomics"]

from typing import Any


def emit_runtime_atomics(emitter: Any) -> None:
    """Emit atomic operations runtime helpers."""
    if not emitter._needs.atomics:
        return

    emitter._output.append("/* Atomic operations - C11 stdatomic */")
    emitter._output.append("#ifndef AILANG_FREESTANDING")
    emitter._output.append("")

    emitter._output.append("static int64_t ailang_atomic_load(volatile int64_t *ptr) {")
    emitter._output.append("    return atomic_load((_Atomic int64_t *)ptr);")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append(
        "static void ailang_atomic_store(volatile int64_t *ptr, int64_t val) {"
    )
    emitter._output.append("    atomic_store((_Atomic int64_t *)ptr, val);")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append(
        "static int64_t ailang_atomic_add(volatile int64_t *ptr, int64_t val) {"
    )
    emitter._output.append("    return atomic_fetch_add((_Atomic int64_t *)ptr, val);")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append(
        "static int64_t ailang_atomic_sub(volatile int64_t *ptr, int64_t val) {"
    )
    emitter._output.append("    return atomic_fetch_sub((_Atomic int64_t *)ptr, val);")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append(
        "static int64_t ailang_atomic_exchange(volatile int64_t *ptr, int64_t val) {"
    )
    emitter._output.append("    return atomic_exchange((_Atomic int64_t *)ptr, val);")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append(
        "static bool ailang_atomic_cas(volatile int64_t *ptr, int64_t expected, int64_t desired) {"
    )
    emitter._output.append(
        "    return atomic_compare_exchange_strong((_Atomic int64_t *)ptr, &expected, desired);"
    )
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append("#endif /* !AILANG_FREESTANDING */")
    emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is
# only imported through import wiring from ``runtime_emitter``.
_exported_emit_runtime_atomics = emit_runtime_atomics
