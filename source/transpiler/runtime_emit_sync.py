"""Runtime emitter helpers for synchronization runtime helpers."""

from __future__ import annotations

__all__ = ["emit_runtime_sync"]

from typing import Any


def emit_runtime_sync(emitter: Any) -> None:
    """Emit synchronization helpers: mutex/condvar/rwlock wrappers."""
    if not emitter._needs.sync:
        return

    emitter._output.append("/* Synchronization primitives - Ada/SPARK-inspired */")
    emitter._output.append("#ifndef AILANG_FREESTANDING")
    emitter._output.append("")

    # --- Mutex ---
    emitter._output.append("static int64_t ailang_mutex_create(void) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    CRITICAL_SECTION *cs = (CRITICAL_SECTION *)ailang_safe_malloc(sizeof(CRITICAL_SECTION));"
    )
    emitter._output.append("    InitializeCriticalSection(cs);")
    emitter._output.append("    return (int64_t)(uintptr_t)cs;")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_t *m = (mtx_t *)ailang_safe_malloc(sizeof(mtx_t));")
    emitter._output.append("    mtx_init(m, mtx_plain);")
    emitter._output.append("    return (int64_t)(uintptr_t)m;")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_mutex_t *m = (pthread_mutex_t *)ailang_safe_malloc(sizeof(pthread_mutex_t));"
    )
    emitter._output.append("    pthread_mutex_init(m, NULL);")
    emitter._output.append("    return (int64_t)(uintptr_t)m;")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_mutex_lock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    EnterCriticalSection((CRITICAL_SECTION *)(uintptr_t)h);"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_lock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append("    pthread_mutex_lock((pthread_mutex_t *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_mutex_unlock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    LeaveCriticalSection((CRITICAL_SECTION *)(uintptr_t)h);"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_unlock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append("    pthread_mutex_unlock((pthread_mutex_t *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_mutex_destroy(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    DeleteCriticalSection((CRITICAL_SECTION *)(uintptr_t)h);"
    )
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_destroy((mtx_t *)(uintptr_t)h);")
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_mutex_destroy((pthread_mutex_t *)(uintptr_t)h);"
    )
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    # --- Condition Variable ---
    emitter._output.append("static int64_t ailang_cond_create(void) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    CONDITION_VARIABLE *cv = (CONDITION_VARIABLE *)ailang_safe_malloc(sizeof(CONDITION_VARIABLE));"
    )
    emitter._output.append("    InitializeConditionVariable(cv);")
    emitter._output.append("    return (int64_t)(uintptr_t)cv;")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    cnd_t *c = (cnd_t *)ailang_safe_malloc(sizeof(cnd_t));")
    emitter._output.append("    cnd_init(c);")
    emitter._output.append("    return (int64_t)(uintptr_t)c;")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_cond_t *c = (pthread_cond_t *)ailang_safe_malloc(sizeof(pthread_cond_t));"
    )
    emitter._output.append("    pthread_cond_init(c, NULL);")
    emitter._output.append("    return (int64_t)(uintptr_t)c;")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_cond_wait(int64_t ch, int64_t mh) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    SleepConditionVariableCS((CONDITION_VARIABLE *)(uintptr_t)ch,"
    )
    emitter._output.append("        (CRITICAL_SECTION *)(uintptr_t)mh, INFINITE);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append(
        "    cnd_wait((cnd_t *)(uintptr_t)ch, (mtx_t *)(uintptr_t)mh);"
    )
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_cond_wait((pthread_cond_t *)(uintptr_t)ch, (pthread_mutex_t *)(uintptr_t)mh);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_cond_signal(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    WakeConditionVariable((CONDITION_VARIABLE *)(uintptr_t)h);"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    cnd_signal((cnd_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append("    pthread_cond_signal((pthread_cond_t *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_cond_broadcast(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    WakeAllConditionVariable((CONDITION_VARIABLE *)(uintptr_t)h);"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    cnd_broadcast((cnd_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_cond_broadcast((pthread_cond_t *)(uintptr_t)h);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_cond_destroy(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    ailang_safe_free((void *)(uintptr_t)h);  /* Windows condvars need no destroy */"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    cnd_destroy((cnd_t *)(uintptr_t)h);")
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append("    pthread_cond_destroy((pthread_cond_t *)(uintptr_t)h);")
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    # --- Read-Write Lock ---
    emitter._output.append("static int64_t ailang_rwlock_create(void) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    SRWLOCK *rw = (SRWLOCK *)ailang_safe_malloc(sizeof(SRWLOCK));"
    )
    emitter._output.append("    InitializeSRWLock(rw);")
    emitter._output.append("    return (int64_t)(uintptr_t)rw;")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append(
        "    /* C11 threads lack rwlock - fallback to mutex for correctness */"
    )
    emitter._output.append("    mtx_t *m = (mtx_t *)ailang_safe_malloc(sizeof(mtx_t));")
    emitter._output.append("    mtx_init(m, mtx_plain);")
    emitter._output.append("    return (int64_t)(uintptr_t)m;")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_t *rw = (pthread_rwlock_t *)ailang_safe_malloc(sizeof(pthread_rwlock_t));"
    )
    emitter._output.append("    pthread_rwlock_init(rw, NULL);")
    emitter._output.append("    return (int64_t)(uintptr_t)rw;")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_rwlock_read_lock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append("    AcquireSRWLockShared((SRWLOCK *)(uintptr_t)h);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_lock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_rdlock((pthread_rwlock_t *)(uintptr_t)h);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_rwlock_write_lock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append("    AcquireSRWLockExclusive((SRWLOCK *)(uintptr_t)h);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_lock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_wrlock((pthread_rwlock_t *)(uintptr_t)h);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_rwlock_read_unlock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append("    ReleaseSRWLockShared((SRWLOCK *)(uintptr_t)h);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_unlock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_unlock((pthread_rwlock_t *)(uintptr_t)h);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_rwlock_write_unlock(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append("    ReleaseSRWLockExclusive((SRWLOCK *)(uintptr_t)h);")
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_unlock((mtx_t *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_unlock((pthread_rwlock_t *)(uintptr_t)h);"
    )
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")

    emitter._output.append("static void ailang_rwlock_destroy(int64_t h) {")
    emitter._output.append("#ifdef AILANG_WIN_THREADS")
    emitter._output.append(
        "    ailang_safe_free((void *)(uintptr_t)h);  /* Windows SRWLock needs no destroy */"
    )
    emitter._output.append("#elif defined(AILANG_C11_THREADS)")
    emitter._output.append("    mtx_destroy((mtx_t *)(uintptr_t)h);")
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#else")
    emitter._output.append(
        "    pthread_rwlock_destroy((pthread_rwlock_t *)(uintptr_t)h);"
    )
    emitter._output.append("    ailang_safe_free((void *)(uintptr_t)h);")
    emitter._output.append("#endif")
    emitter._output.append("}")
    emitter._output.append("")
    emitter._output.append("#endif /* !AILANG_FREESTANDING */")
    emitter._output.append("")


# Keep strict static checks from reporting this module as dead code when it is
# only imported through wiring in ``runtime_emitter``.
_exported_emit_runtime_sync = emit_runtime_sync
