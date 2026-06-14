"""Runtime emitter helpers for runtime emit threading."""

from __future__ import annotations

__all__ = ["emit_runtime_threading"]


def emit_runtime_threading(self) -> None:
    """Emit threading runtime helpers (spawn/join)."""
    if not self._needs.threading:
        return

    self._output.append("/* Threading runtime - cross-platform */")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("")

    # Thread handle type
    self._output.append("typedef struct {")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    thrd_t thread;")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    HANDLE thread;")
    self._output.append("#else")
    self._output.append("    pthread_t thread;")
    self._output.append("#endif")
    self._output.append("    int64_t result;")
    self._output.append("    volatile int done;")
    self._output.append("} ailang_thread_t;")
    self._output.append("")

    # Thread wrapper function type
    self._output.append("typedef int64_t (*ailang_thread_func_t)(void *);")
    self._output.append("")

    # Thread entry wrapper
    self._output.append("typedef struct {")
    self._output.append("    ailang_thread_func_t func;")
    self._output.append("    void *arg;")
    self._output.append("    ailang_thread_t *handle;")
    self._output.append("} ailang_thread_arg_t;")
    self._output.append("")

    # Thread entry function - cross-platform
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("static int ailang_thread_entry(void *arg) {")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("static DWORD WINAPI ailang_thread_entry(LPVOID arg) {")
    self._output.append("#else")
    self._output.append("static void *ailang_thread_entry(void *arg) {")
    self._output.append("#endif")
    self._output.append("    ailang_thread_arg_t *targ = (ailang_thread_arg_t *)arg;")
    self._output.append("    targ->handle->result = targ->func(targ->arg);")
    self._output.append("    targ->handle->done = 1;")
    self._output.append("    free(targ);")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    return 0;")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    return 0;")
    self._output.append("#else")
    self._output.append("    return nullptr;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")

    # Spawn function
    self._output.append(
        "static ailang_thread_t *ailang_spawn(ailang_thread_func_t func, void *arg) {"
    )
    self._output.append(
        "    ailang_thread_t *handle = (ailang_thread_t *)malloc(sizeof(ailang_thread_t));"
    )
    self._output.append("    if (!handle) return nullptr;")
    self._output.append("    handle->result = 0;")
    self._output.append("    handle->done = 0;")
    self._output.append(
        "    ailang_thread_arg_t *targ = (ailang_thread_arg_t *)malloc(sizeof(ailang_thread_arg_t));"
    )
    self._output.append("    if (!targ) { free(handle); return nullptr; }")
    self._output.append("    targ->func = func;")
    self._output.append("    targ->arg = arg;")
    self._output.append("    targ->handle = handle;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append(
        "    if (thrd_create(&handle->thread, ailang_thread_entry, targ) != thrd_success) {"
    )
    self._output.append("        free(targ); free(handle); return nullptr;")
    self._output.append("    }")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append(
        "    handle->thread = CreateThread(NULL, 0, ailang_thread_entry, targ, 0, NULL);"
    )
    self._output.append(
        "    if (!handle->thread) { free(targ); free(handle); return nullptr; }"
    )
    self._output.append("#else")
    self._output.append(
        "    if (pthread_create(&handle->thread, NULL, ailang_thread_entry, targ) != 0) {"
    )
    self._output.append("        free(targ); free(handle); return nullptr;")
    self._output.append("    }")
    self._output.append("#endif")
    self._output.append("    return handle;")
    self._output.append("}")
    self._output.append("")

    # Join function
    self._output.append("static int64_t ailang_join(ailang_thread_t *handle) {")
    self._output.append("    if (!handle) return 0;")
    self._output.append("#ifdef AILANG_C11_THREADS")
    self._output.append("    thrd_join(handle->thread, NULL);")
    self._output.append("#elif defined(AILANG_WIN_THREADS)")
    self._output.append("    WaitForSingleObject(handle->thread, INFINITE);")
    self._output.append("    CloseHandle(handle->thread);")
    self._output.append("#else")
    self._output.append("    pthread_join(handle->thread, NULL);")
    self._output.append("#endif")
    self._output.append("    int64_t result = handle->result;")
    self._output.append("    free(handle);")
    self._output.append("    return result;")
    self._output.append("}")
    self._output.append("")
    # Per-target spawn glue: for each `spawn func(arg1, arg2, ...)`
    # site we generate a (box struct, thunk wrapper, caller helper)
    # triple so the args survive the trip across the thread boundary.
    # The box is a heap-allocated struct holding the captured args;
    # the thunk runs on the new thread, unboxes, calls `func`, and
    # frees the box; the caller helper is what the spawn expression
    # ultimately compiles to so the call site stays a single C
    # expression (no GCC statement-expression extension needed).
    self._emit_spawn_thunks()
    self._output.append("#endif /* !AILANG_FREESTANDING */")
    self._output.append("")


def _spawn_box_name(self, func_name: str) -> str:
    return f"__ailang_spawn_box_{func_name}_t"


def _spawn_thunk_name(self, func_name: str) -> str:
    return f"__ailang_spawn_thunk_{func_name}"


def _spawn_caller_name(self, func_name: str) -> str:
    return f"__ailang_spawn_call_{func_name}"


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = emit_runtime_threading
