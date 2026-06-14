"""Runtime emitter helpers for runtime emit channels."""

from __future__ import annotations

__all__ = ["emit_runtime_channels"]


def emit_runtime_channels(self) -> None:
    """Emit channel runtime helpers for message passing."""
    if not self._needs.channels:
        return

    self._output.append("/* Channel runtime - thread-safe message passing */")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("")

    # Channel structure
    self._output.append("typedef struct {")
    self._output.append("    int64_t *buffer;")
    self._output.append("    int64_t capacity;")
    self._output.append("    int64_t head;")
    self._output.append("    int64_t tail;")
    self._output.append("    int64_t count;")
    self._output.append("    volatile int closed;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_t mutex;")
    self._output.append("    cnd_t not_empty;")
    self._output.append("    cnd_t not_full;")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    CRITICAL_SECTION mutex;")
    self._output.append("    CONDITION_VARIABLE not_empty;")
    self._output.append("    CONDITION_VARIABLE not_full;")
    self._output.append("#else")
    self._output.append("    pthread_mutex_t mutex;")
    self._output.append("    pthread_cond_t not_empty;")
    self._output.append("    pthread_cond_t not_full;")
    self._output.append("#endif")
    self._output.append("} ailang_channel_t;")
    self._output.append("")

    # Channel create. Use ailang_safe_malloc so the tracker sees
    # the channel struct + buffer; the tracked free in
    # ailang_channel_close keeps counters symmetric.
    self._output.append(
        "static ailang_channel_t *ailang_channel_create(int64_t capacity) {"
    )
    self._output.append(
        "    ailang_channel_t *ch = (ailang_channel_t *)"
        "ailang_safe_malloc(sizeof(ailang_channel_t));"
    )
    self._output.append("    if (!ch) return nullptr;")
    self._output.append("    ch->capacity = capacity > 0 ? capacity : 1;")
    self._output.append(
        "    ch->buffer = (int64_t *)ailang_safe_malloc("
        "ch->capacity * sizeof(int64_t));"
    )
    self._output.append(
        "    if (!ch->buffer) { ailang_safe_free(ch); return nullptr; }"
    )
    self._output.append("    ch->head = ch->tail = ch->count = 0;")
    self._output.append("    ch->closed = 0;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_init(&ch->mutex, mtx_plain);")
    self._output.append("    cnd_init(&ch->not_empty);")
    self._output.append("    cnd_init(&ch->not_full);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    InitializeCriticalSection(&ch->mutex);")
    self._output.append("    InitializeConditionVariable(&ch->not_empty);")
    self._output.append("    InitializeConditionVariable(&ch->not_full);")
    self._output.append("#else")
    self._output.append("    pthread_mutex_init(&ch->mutex, NULL);")
    self._output.append("    pthread_cond_init(&ch->not_empty, NULL);")
    self._output.append("    pthread_cond_init(&ch->not_full, NULL);")
    self._output.append("#endif")
    self._output.append("    return ch;")
    self._output.append("}")
    self._output.append("")

    # Channel send (blocking)
    self._output.append(
        "static void ailang_channel_send(ailang_channel_t *ch, int64_t value) {"
    )
    self._output.append("    if (!ch || ch->closed) return;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_lock(&ch->mutex);")
    self._output.append(
        "    while (ch->count >= ch->capacity && !ch->closed) cnd_wait(&ch->not_full, &ch->mutex);"
    )
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    EnterCriticalSection(&ch->mutex);")
    self._output.append(
        "    while (ch->count >= ch->capacity && !ch->closed) "
        "SleepConditionVariableCS(&ch->not_full, &ch->mutex, INFINITE);"
    )
    self._output.append("#else")
    self._output.append("    pthread_mutex_lock(&ch->mutex);")
    self._output.append(
        "    while (ch->count >= ch->capacity && !ch->closed) "
        "pthread_cond_wait(&ch->not_full, &ch->mutex);"
    )
    self._output.append("#endif")
    self._output.append("    if (!ch->closed) {")
    self._output.append("        ch->buffer[ch->tail] = value;")
    self._output.append("        ch->tail = (ch->tail + 1) % ch->capacity;")
    self._output.append("        ch->count++;")
    self._output.append("    }")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    cnd_signal(&ch->not_empty);")
    self._output.append("    mtx_unlock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    WakeConditionVariable(&ch->not_empty);")
    self._output.append("    LeaveCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    pthread_cond_signal(&ch->not_empty);")
    self._output.append("    pthread_mutex_unlock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")

    # Channel receive (blocking)
    self._output.append("static int64_t ailang_channel_recv(ailang_channel_t *ch) {")
    self._output.append("    if (!ch) return 0;")
    self._output.append("    int64_t value = 0;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_lock(&ch->mutex);")
    self._output.append(
        "    while (ch->count == 0 && !ch->closed) cnd_wait(&ch->not_empty, &ch->mutex);"
    )
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    EnterCriticalSection(&ch->mutex);")
    self._output.append(
        "    while (ch->count == 0 && !ch->closed) SleepConditionVariableCS(&ch->not_empty, &ch->mutex, INFINITE);"
    )
    self._output.append("#else")
    self._output.append("    pthread_mutex_lock(&ch->mutex);")
    self._output.append(
        "    while (ch->count == 0 && !ch->closed) pthread_cond_wait(&ch->not_empty, &ch->mutex);"
    )
    self._output.append("#endif")
    self._output.append("    if (ch->count > 0) {")
    self._output.append("        value = ch->buffer[ch->head];")
    self._output.append("        ch->head = (ch->head + 1) % ch->capacity;")
    self._output.append("        ch->count--;")
    self._output.append("    }")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    cnd_signal(&ch->not_full);")
    self._output.append("    mtx_unlock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    WakeConditionVariable(&ch->not_full);")
    self._output.append("    LeaveCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    pthread_cond_signal(&ch->not_full);")
    self._output.append("    pthread_mutex_unlock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    return value;")
    self._output.append("}")
    self._output.append("")

    # Channel try_send (non-blocking)
    self._output.append(
        "static bool ailang_channel_try_send(ailang_channel_t *ch, int64_t value) {"
    )
    self._output.append("    if (!ch || ch->closed) return false;")
    self._output.append("    bool sent = false;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_lock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    EnterCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    pthread_mutex_lock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    if (ch->count < ch->capacity && !ch->closed) {")
    self._output.append("        ch->buffer[ch->tail] = value;")
    self._output.append("        ch->tail = (ch->tail + 1) % ch->capacity;")
    self._output.append("        ch->count++;")
    self._output.append("        sent = true;")
    self._output.append("    }")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    if (sent) cnd_signal(&ch->not_empty);")
    self._output.append("    mtx_unlock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    if (sent) WakeConditionVariable(&ch->not_empty);")
    self._output.append("    LeaveCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    if (sent) pthread_cond_signal(&ch->not_empty);")
    self._output.append("    pthread_mutex_unlock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    return sent;")
    self._output.append("}")
    self._output.append("")

    # Channel try_recv (non-blocking)
    self._output.append(
        "static int64_t ailang_channel_try_recv(ailang_channel_t *ch, bool *success) {"
    )
    self._output.append("    if (!ch) { *success = false; return 0; }")
    self._output.append("    int64_t value = 0;")
    self._output.append("    *success = false;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_lock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    EnterCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    pthread_mutex_lock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    if (ch->count > 0) {")
    self._output.append("        value = ch->buffer[ch->head];")
    self._output.append("        ch->head = (ch->head + 1) % ch->capacity;")
    self._output.append("        ch->count--;")
    self._output.append("        *success = true;")
    self._output.append("    }")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    if (*success) cnd_signal(&ch->not_full);")
    self._output.append("    mtx_unlock(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    if (*success) WakeConditionVariable(&ch->not_full);")
    self._output.append("    LeaveCriticalSection(&ch->mutex);")
    self._output.append("#else")
    self._output.append("    if (*success) pthread_cond_signal(&ch->not_full);")
    self._output.append("    pthread_mutex_unlock(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    return value;")
    self._output.append("}")
    self._output.append("")

    # Channel close. Sets closed=1, wakes all waiters, then tears
    # down OS primitives and releases the heap. The CALLER is
    # responsible for ensuring no other thread is still holding a
    # reference -- there's no refcount, so close-while-in-use is
    # UAF. Matches the lifetime contract of mutex_destroy/cond_destroy.
    self._output.append("static void ailang_channel_close(ailang_channel_t *ch) {")
    self._output.append("    if (!ch) return;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    mtx_lock(&ch->mutex);")
    self._output.append("    ch->closed = 1;")
    self._output.append("    cnd_broadcast(&ch->not_empty);")
    self._output.append("    cnd_broadcast(&ch->not_full);")
    self._output.append("    mtx_unlock(&ch->mutex);")
    self._output.append("    cnd_destroy(&ch->not_empty);")
    self._output.append("    cnd_destroy(&ch->not_full);")
    self._output.append("    mtx_destroy(&ch->mutex);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    EnterCriticalSection(&ch->mutex);")
    self._output.append("    ch->closed = 1;")
    self._output.append("    WakeAllConditionVariable(&ch->not_empty);")
    self._output.append("    WakeAllConditionVariable(&ch->not_full);")
    self._output.append("    LeaveCriticalSection(&ch->mutex);")
    self._output.append("    DeleteCriticalSection(&ch->mutex);")
    self._output.append(
        "    /* Win32 condition variables are stack-resident and" " need no destroy. */"
    )
    self._output.append("#else")
    self._output.append("    pthread_mutex_lock(&ch->mutex);")
    self._output.append("    ch->closed = 1;")
    self._output.append("    pthread_cond_broadcast(&ch->not_empty);")
    self._output.append("    pthread_cond_broadcast(&ch->not_full);")
    self._output.append("    pthread_mutex_unlock(&ch->mutex);")
    self._output.append("    pthread_cond_destroy(&ch->not_empty);")
    self._output.append("    pthread_cond_destroy(&ch->not_full);")
    self._output.append("    pthread_mutex_destroy(&ch->mutex);")
    self._output.append("#endif")
    self._output.append("    ailang_safe_free(ch->buffer);")
    self._output.append("    ailang_safe_free(ch);")
    self._output.append("}")
    self._output.append("")
    self._output.append("#endif /* !AILANG_FREESTANDING */")
    self._output.append("")


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = emit_runtime_channels
