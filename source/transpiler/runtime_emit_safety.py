"""Runtime emitter helpers for runtime emit safety."""

from __future__ import annotations

from transpiler.runtime_emit_safety_tail import emit_safety_tail_helpers

__all__ = ["emit_safety_helpers"]


def emit_safety_helpers(self) -> None:
    """Emit C23 safety helpers for bounds checking and overflow detection."""
    # Safety trap: catchable safety error or fatal exit. The
    # __ailang_trap_msg flag lets the leak reporter distinguish a
    # trap-aborted exit (cleanup code never ran -- "live" bytes are
    # not a leak) from a normal exit with leaked memory.
    # Abnormal-exit flags cover Ctrl+C/console-close/SIGTERM cleanup skips.
    self._output.append("/* Active Armor: catchable safety errors */")
    self._output.append("AILANG_UNUSED static int __ailang_in_try = 0;")
    self._output.append("AILANG_UNUSED static const char *__ailang_trap_msg = NULL;")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("static volatile sig_atomic_t __ailang_abnormal_exit = 0;")
    self._output.append("static volatile sig_atomic_t __ailang_abnormal_code = 0;")
    self._output.append("static void __ailang_signal_exit_handler(int sig) {")
    self._output.append("    __ailang_abnormal_exit = 1;")
    self._output.append("    __ailang_abnormal_code = (sig_atomic_t)sig;")
    self._output.append("    exit(128 + sig);")
    self._output.append("}")
    self._output.append("#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)")
    self._output.append(
        "static BOOL WINAPI __ailang_console_ctrl_handler(DWORD event_type) {"
    )
    self._output.append("    __ailang_abnormal_exit = 1;")
    self._output.append(
        "    __ailang_abnormal_code = (sig_atomic_t)(1000u + event_type);"
    )
    self._output.append("    exit(128);")
    self._output.append("    return TRUE;")
    self._output.append("}")
    self._output.append("#endif")
    self._output.append(
        "AILANG_UNUSED static void __ailang_install_abnormal_exit_handlers(void) {"
    )
    self._output.append("    signal(SIGINT, __ailang_signal_exit_handler);")
    self._output.append("    signal(SIGTERM, __ailang_signal_exit_handler);")
    self._output.append("#ifdef SIGHUP")
    self._output.append("    signal(SIGHUP, __ailang_signal_exit_handler);")
    self._output.append("#endif")
    self._output.append("#ifdef SIGQUIT")
    self._output.append("    signal(SIGQUIT, __ailang_signal_exit_handler);")
    self._output.append("#endif")
    self._output.append("#if defined(_WIN32) || defined(_WIN64) || defined(__CYGWIN__)")
    self._output.append(
        "    SetConsoleCtrlHandler(__ailang_console_ctrl_handler, TRUE);"
    )
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("#else")
    self._output.append(
        "AILANG_UNUSED static void __ailang_install_abnormal_exit_handlers(void) { }"
    )
    self._output.append("#endif")
    self._output.append("")
    self._output.append("static void __ailang_safety_trap(const char *msg) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (__ailang_in_try) {")
    self._output.append("        __ailang_exc_msg = msg;")
    self._output.append("        __ailang_exc_type = 1355569627u;  /* SafetyError */")
    self._output.append("        longjmp(__ailang_exc_jmpbuf, 1);")
    self._output.append("    }")
    self._output.append("    __ailang_trap_msg = msg;")
    self._output.append("    exit(1);")
    self._output.append("#else")
    self._output.append("    (void)msg;")
    self._output.append("    for (;;) { }")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append("/* Hosted/freestanding string primitive bridge. */")
    self._output.append(
        "AILANG_UNUSED static size_t __ailang_strlen_raw(const char *s) {"
    )
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    return strlen(s);")
    self._output.append("#else")
    self._output.append("    size_t len = 0;")
    self._output.append("    while (s[len] != '\\0') len++;")
    self._output.append("    return len;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static int __ailang_strcmp_raw(const char *a, const char *b) {"
    )
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    return strcmp(a, b);")
    self._output.append("#else")
    self._output.append("    const unsigned char *pa = (const unsigned char *)a;")
    self._output.append("    const unsigned char *pb = (const unsigned char *)b;")
    self._output.append("    while (*pa != 0 && *pa == *pb) { pa++; pb++; }")
    self._output.append("    return (int)*pa - (int)*pb;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append("AILANG_UNUSED static int __ailang_putchar_raw(int c) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    return putchar(c);")
    self._output.append("#else")
    self._output.append("    (void)c;")
    self._output.append("    return -1;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append("AILANG_UNUSED static int __ailang_puts_raw(const char *s) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    return puts(s);")
    self._output.append("#else")
    self._output.append("    (void)s;")
    self._output.append("    return -1;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static void *__ailang_memcpy_raw("
        "void *dst, const void *src, size_t n) {"
    )
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    return memcpy(dst, src, n);")
    self._output.append("#else")
    self._output.append("    unsigned char *d = (unsigned char *)dst;")
    self._output.append("    const unsigned char *s = (const unsigned char *)src;")
    self._output.append("    for (size_t i = 0; i < n; i++) d[i] = s[i];")
    self._output.append("    return dst;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    # Null-guarded peek/poke helpers (Active Armor Item 3)
    self._output.append("/* Null-guarded memory access */")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_peek64(int64_t ptr, int64_t off) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in peek64\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in peek64");'
    )
    self._output.append("    }")
    self._output.append("    return *(int64_t *)((uintptr_t)ptr + (size_t)off * 8);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_poke64"
        "(int64_t ptr, int64_t off, int64_t val) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in poke64\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in poke64");'
    )
    self._output.append("    }")
    self._output.append("    *(int64_t *)((uintptr_t)ptr + (size_t)off * 8) = val;")
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")
    # 32-bit peek/poke for framebuffer operations (uint32_t indexed access)
    self._output.append("/* 32-bit memory access (framebuffers, pixel ops) */")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_peek32(int64_t ptr, int64_t off) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in peek32\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in peek32");'
    )
    self._output.append("    }")
    self._output.append(
        "    return (int64_t)(*(uint32_t *)((uintptr_t)ptr + (size_t)off * 4));"
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_poke32"
        "(int64_t ptr, int64_t off, int64_t val) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in poke32\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in poke32");'
    )
    self._output.append("    }")
    self._output.append(
        "    *(uint32_t *)((uintptr_t)ptr + (size_t)off * 4) = (uint32_t)val;"
    )
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")
    # 8-bit peek/poke for byte-level access (strings, binary data)
    self._output.append("/* 8-bit memory access (bytes, characters) */")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_peek8(int64_t ptr, int64_t off) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in peek8\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in peek8");'
    )
    self._output.append("    }")
    self._output.append(
        "    return (int64_t)(*(uint8_t *)((uintptr_t)ptr + (size_t)off));"
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static int64_t ailang_poke8"
        "(int64_t ptr, int64_t off, int64_t val) {"
    )
    self._output.append("    if (ptr == 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: null pointer dereference in poke8\\n");'
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("Null pointer dereference in poke8");'
    )
    self._output.append("    }")
    self._output.append(
        "    *(uint8_t *)((uintptr_t)ptr + (size_t)off) = (uint8_t)val;"
    )
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")

    # Memory allocation limits - prevents allocation bombs.
    #
    # The cap MUST reflect *live* heap, not lifetime mallocs. A naive
    # cumulative counter traps any long-running program after 4 GB of
    # cumulative allocations, even if everything was freed correctly.
    # Track frees too so the cap is a real RSS proxy.
    self._output.append("/* C23 Safety: Memory allocation limits */")
    self._output.append("#ifndef AILANG_MAX_ALLOC_SIZE")
    self._output.append(
        "#define AILANG_MAX_ALLOC_SIZE (1024LL * 1024LL * 1024LL)  /* 1 GB */"
    )
    self._output.append("#endif")
    self._output.append("")
    self._output.append("#ifndef AILANG_TRACK_ALLOCATIONS")
    self._output.append("#define AILANG_TRACK_ALLOCATIONS 1")
    self._output.append("#endif")
    self._output.append("")
    self._output.append("AILANG_UNUSED static size_t __ailang_total_allocated = 0;")
    self._output.append("AILANG_UNUSED static size_t __ailang_total_freed = 0;")
    self._output.append("#ifndef AILANG_MAX_TOTAL_ALLOC")
    self._output.append(
        "#define AILANG_MAX_TOTAL_ALLOC (4LL * 1024LL * 1024LL * 1024LL)  /* 4 GB */"
    )
    self._output.append("#endif")
    self._output.append("")
    # Platform-specific malloc-block-size query.
    self._output.append(
        "/* Query the actual size of an allocation (for free-tracking). */"
    )
    self._output.append("#if defined(AILANG_FREESTANDING)")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) ((size_t)0)")
    self._output.append("#elif defined(_WIN32)")
    self._output.append("#include <malloc.h>")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) _msize(p)")
    self._output.append("#elif defined(__APPLE__)")
    self._output.append("#include <malloc/malloc.h>")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) malloc_size(p)")
    self._output.append("#elif defined(__FreeBSD__)")
    self._output.append("#include <malloc_np.h>")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) malloc_usable_size(p)")
    self._output.append("#elif defined(__GLIBC__) || defined(__linux__)")
    self._output.append("#include <malloc.h>")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) malloc_usable_size(p)")
    self._output.append("#else")
    self._output.append("/* Fallback: don't track (cap stays as a lifetime cap). */")
    self._output.append("#define AILANG_MALLOC_USABLE_SIZE(p) ((size_t)0)")
    self._output.append("#endif")
    self._output.append("")
    self._output.append("#ifdef AILANG_FREESTANDING")
    self._output.append("AILANG_UNUSED static void *ailang_safe_malloc(size_t size) {")
    self._output.append("    (void)size;")
    self._output.append(
        '    __ailang_safety_trap("allocation unavailable in freestanding mode");'
    )
    self._output.append("    return NULL;")
    self._output.append("}")
    self._output.append(
        "AILANG_UNUSED static void *ailang_safe_calloc(size_t n, size_t size) {"
    )
    self._output.append("    (void)n; (void)size;")
    self._output.append(
        '    __ailang_safety_trap("allocation unavailable in freestanding mode");'
    )
    self._output.append("    return NULL;")
    self._output.append("}")
    self._output.append(
        "AILANG_UNUSED static void *ailang_safe_realloc(void *ptr, size_t size) {"
    )
    self._output.append("    (void)ptr; (void)size;")
    self._output.append(
        '    __ailang_safety_trap("allocation unavailable in freestanding mode");'
    )
    self._output.append("    return NULL;")
    self._output.append("}")
    self._output.append("AILANG_UNUSED static void ailang_safe_free(void *ptr) {")
    self._output.append("    (void)ptr;")
    self._output.append("}")
    self._output.append("#else")
    self._output.append("AILANG_UNUSED static void *ailang_safe_malloc(size_t size) {")
    self._output.append("    if (size > AILANG_MAX_ALLOC_SIZE) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: allocation size %zu exceeds limit %lld\\n", '
        "size, (long long)AILANG_MAX_ALLOC_SIZE);"
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("allocation size exceeds limit");'
    )
    self._output.append("    }")
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append(
        "    size_t live = __ailang_total_allocated - __ailang_total_freed;"
    )
    self._output.append("    if (live + size > AILANG_MAX_TOTAL_ALLOC) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: live allocation would exceed %lld bytes\\n", '
        "(long long)AILANG_MAX_TOTAL_ALLOC);"
    )
    self._output.append("#endif")
    self._output.append(
        '        __ailang_safety_trap("live allocation exceeds limit");'
    )
    self._output.append("    }")
    self._output.append("#endif")
    self._output.append("    void *ptr = malloc(size);")
    self._output.append("    if (!ptr && size > 0) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        '        fprintf(stderr, "Error: memory allocation failed for %zu bytes\\n", size);'
    )
    self._output.append("#endif")
    self._output.append('        __ailang_safety_trap("memory allocation failed");')
    self._output.append("    }")
    # Track ACTUAL allocation size (with platform alignment padding),
    # not requested size. ailang_safe_free reads back actual size via
    # AILANG_MALLOC_USABLE_SIZE, so allocated/freed counters must use
    # the same metric -- otherwise glibc's chunk overhead causes
    # `freed > allocated` after one cycle, wrapping `live` to a huge
    # unsigned value and tripping the cap on the next allocation.
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append("    size_t actual = AILANG_MALLOC_USABLE_SIZE(ptr);")
    self._output.append("    __ailang_total_allocated += (actual ? actual : size);")
    self._output.append("#endif")
    self._output.append("    return ptr;")
    self._output.append("}")
    self._output.append("")
    # Tracked calloc. The user-callable `calloc(n, sz)` routes
    # here so the tracker sees the allocation; without this, the
    # paired `free` subtracts bytes the counter never added and
    # `live` underflows to a huge unsigned value, spuriously
    # tripping the live cap on the next allocation.
    self._output.append(
        "AILANG_UNUSED static void *ailang_safe_calloc(size_t n, size_t size) {"
    )
    self._output.append("    size_t total;")
    self._output.append("    if (size != 0 && n > AILANG_MAX_ALLOC_SIZE / size) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append('        fprintf(stderr, "Error: calloc size overflow\\n");')
    self._output.append("#endif")
    self._output.append('        __ailang_safety_trap("calloc size overflow");')
    self._output.append("    }")
    self._output.append("    total = n * size;")
    self._output.append("    void *ptr = ailang_safe_malloc(total);")
    self._output.append("    if (ptr) memset(ptr, 0, total);")
    self._output.append("    return ptr;")
    self._output.append("}")
    self._output.append("")
    # Per-request arena routing. When `__ailang_request_arena` is set
    # (via the `arena_use(handle)` builtin), the hot-path string
    # helpers below route through it instead of ailang_safe_malloc,
    # so a single `arena_reset` between requests reclaims everything
    # they produced. The struct layout matches AilangArena exactly,
    # so handles from `arena_create(size)` can be passed in directly.
    #
    # Storage class: `_Thread_local` only when the program uses
    # `spawn` (so multiple threads might call `arena_use(my_arena)`
    # concurrently and need isolated routing). For single-threaded
    # programs, plain `static` saves a measurable amount per access
    # (TLS goes through fs/gs segment register on x86-64; static is
    # a direct load). adapt_serve is single-threaded -- without this
    # gate it paid ~30% throughput for safety it doesn't use yet.
    tls_qualifier = "_Thread_local " if self._needs.threading else ""
    self._output.append("typedef struct __ailang_arena_block {")
    self._output.append("    char *base;")
    self._output.append("    char *current;")
    self._output.append("    char *end;")
    self._output.append("} __ailang_arena_block;")
    self._output.append(f"static {tls_qualifier}void *__ailang_request_arena = NULL;")
    # Arena graveyard: ring buffer of recently-destroyed arena
    # ranges, per-thread. The auto-cleanup pass at function exit
    # may still emit `ailang_safe_free` calls for owned-string
    # locals that were allocated inside an arena that the function
    # has explicitly destroyed (e.g. `arena_destroy(a); return n`
    # -- auto-cleanup of `s` runs AFTER arena_destroy). Without the
    # graveyard, those pointers fall through the active-arena
    # range check, hit `free()` on memory that's not a malloc
    # head, and glibc reports `free(): invalid pointer`. The
    # graveyard remembers the ranges of the last few destroyed
    # arenas so safe_free can recognize and skip them.
    self._output.append("typedef struct {")
    self._output.append("    char *base;")
    self._output.append("    char *end;")
    self._output.append("} __ailang_arena_grave_t;")
    self._output.append("#define AILANG_ARENA_GRAVEYARD_SIZE 8")
    self._output.append(
        f"static {tls_qualifier}__ailang_arena_grave_t"
        " __ailang_arena_graveyard[AILANG_ARENA_GRAVEYARD_SIZE];"
    )
    self._output.append(
        f"AILANG_UNUSED static {tls_qualifier}int __ailang_arena_graveyard_idx = 0;"
    )
    # Counter: number of arenas this thread has ever destroyed.
    # safe_free's graveyard scan can short-circuit when this is 0,
    # which is the common case for long-lived servers like
    # adapt_serve (per-request arena_reset, never arena_destroy).
    # Without this short-circuit, every safe_free pays an 8-iter
    # loop on the hot path -- measurably halves throughput.
    self._output.append(
        f"static {tls_qualifier}int __ailang_arena_graveyard_filled = 0;"
    )
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static void *ailang_request_alloc(size_t size) {"
    )
    self._output.append("    if (__ailang_request_arena) {")
    self._output.append(
        "        __ailang_arena_block *a ="
        " (__ailang_arena_block *)__ailang_request_arena;"
    )
    self._output.append("        if ((size_t)(a->end - a->current) >= size) {")
    self._output.append("            void *p = a->current;")
    self._output.append("            a->current += size;")
    self._output.append("            return p;")
    self._output.append("        }")
    self._output.append(
        "        /* Arena full -- fall through to malloc to avoid trapping. */"
    )
    self._output.append("    }")
    self._output.append("    return ailang_safe_malloc(size);")
    self._output.append("}")
    self._output.append("")
    # Tracked realloc. Routes array_push's growth path through the
    # counter so `mem_used()` reflects array allocations honestly.
    self._output.append(
        "AILANG_UNUSED static void *ailang_safe_realloc(void *ptr, size_t size) {"
    )
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append(
        "    size_t old_size = ptr ? AILANG_MALLOC_USABLE_SIZE(ptr) : 0;"
    )
    self._output.append(
        "    size_t live = __ailang_total_allocated - __ailang_total_freed;"
    )
    self._output.append("    if (live + size - old_size > AILANG_MAX_TOTAL_ALLOC) {")
    self._output.append(
        '        __ailang_safety_trap("live allocation exceeds limit");'
    )
    self._output.append("    }")
    self._output.append("#endif")
    self._output.append("    void *new_ptr = realloc(ptr, size);")
    self._output.append("    if (!new_ptr && size > 0) {")
    self._output.append('        __ailang_safety_trap("memory allocation failed");')
    self._output.append("    }")
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append("    __ailang_total_freed += old_size;")
    # Track ACTUAL post-realloc size, matching safe_free semantics.
    self._output.append("    size_t new_actual = AILANG_MALLOC_USABLE_SIZE(new_ptr);")
    self._output.append(
        "    __ailang_total_allocated += (new_actual ? new_actual : size);"
    )
    self._output.append("#endif")
    self._output.append("    return new_ptr;")
    self._output.append("}")
    self._output.append("")
    # Tracked free counterpart. Internal runtime free()s and the
    # user-callable `free()` builtin route through this. Two safety
    # guards:
    #   1. NULL is a no-op (matches free() semantics).
    #   2. If `ptr` lives inside the active per-request arena, this
    #      MUST be a no-op -- arena memory is reclaimed in bulk by
    #      arena_reset, and individually free()ing into the middle
    #      of the arena's chunk would corrupt the heap.
    self._output.append("AILANG_UNUSED static void ailang_safe_free(void *ptr) {")
    self._output.append("    if (!ptr) return;")
    self._output.append("    if (__ailang_request_arena) {")
    self._output.append(
        "        __ailang_arena_block *__a ="
        " (__ailang_arena_block *)__ailang_request_arena;"
    )
    self._output.append(
        "        if ((char *)ptr >= __a->base && (char *)ptr < __a->end) {"
    )
    self._output.append(
        "            return;  /* arena memory: reclaimed by arena_reset */"
    )
    self._output.append("        }")
    self._output.append("    }")
    # Graveyard check: pointer was alloc'd in an arena that has
    # since been destroyed. Skipping the free is correct -- the
    # arena's underlying buffer was already freed when the arena
    # was destroyed; the pointer is a substring inside that
    # already-released region. Calling free() here would corrupt
    # the heap (glibc raises "free(): invalid pointer").
    #
    # Hot-path short-circuit: skip the loop entirely if no arena
    # has ever been destroyed in this thread. adapt_serve creates
    # one request_arena and only ever resets it, so this branch is
    # the common case and saves ~8 loads + 8 compares per free.
    self._output.append("    if (__ailang_arena_graveyard_filled) {")
    self._output.append(
        "        for (int __gi = 0; __gi < AILANG_ARENA_GRAVEYARD_SIZE; __gi++) {"
    )
    self._output.append("            char *__gb = __ailang_arena_graveyard[__gi].base;")
    self._output.append("            char *__ge = __ailang_arena_graveyard[__gi].end;")
    self._output.append(
        "            if (__gb && (char *)ptr >= __gb && (char *)ptr < __ge) {"
    )
    self._output.append("                return;")
    self._output.append("            }")
    self._output.append("        }")
    self._output.append("    }")
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append("    size_t sz = AILANG_MALLOC_USABLE_SIZE(ptr);")
    self._output.append("    __ailang_total_freed += sz;")
    self._output.append("#endif")
    self._output.append("    free(ptr);")
    self._output.append("}")
    self._output.append("#endif")
    self._output.append("")

    # Definitions for the dyn_array / str_array free helpers that
    # were forward-declared in _emit_dynamic_collection_typedefs.
    # ailang_safe_free is now in scope, so we can emit them here.
    self._output.append(
        "AILANG_UNUSED static void ailang_dyn_array_free(" "ailang_dyn_array *arr) {"
    )
    self._output.append("    if (!arr || !arr->data) return;")
    self._output.append("    ailang_safe_free(arr->data);")
    self._output.append("    arr->data = NULL; arr->length = 0; arr->capacity = 0;")
    self._output.append("}")
    self._output.append(
        "AILANG_UNUSED static void ailang_str_array_free_v2(" "ailang_str_array *arr) {"
    )
    self._output.append("    if (!arr || !arr->data) return;")
    self._output.append("    ailang_safe_free((void *)arr->data);")
    self._output.append("    arr->data = NULL; arr->length = 0; arr->capacity = 0;")
    self._output.append("}")
    self._output.append("")

    # Built-in leak reporter. Fires at process exit (registered via
    # atexit at the top of main). Three behaviors:
    #   - Default: silent if zero live bytes; print a warning if any
    #     bytes are still allocated at exit (real or intentionally
    #     long-lived state).
    #   - AILANG_LEAK_REPORT=1: always print the summary regardless.
    #   - AILANG_LEAK_REPORT=0 (or unset env, no leak): silent.
    # Long-running programs can also poll `mem_used()` at runtime for
    # custom diagnostics -- same counters this report reads.
    self._output.append("AILANG_UNUSED static void __ailang_leak_report(void) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append(
        "    size_t live = __ailang_total_allocated - __ailang_total_freed;"
    )
    self._output.append("#else")
    self._output.append("    size_t live = 0;")
    self._output.append("#endif")
    self._output.append('    const char *verbose = getenv("AILANG_LEAK_REPORT");')
    self._output.append("    int disabled = verbose && verbose[0] == '0';")
    self._output.append("    int forced = verbose && verbose[0] && verbose[0] != '0';")
    self._output.append(
        "    if (live == 0 && !forced && !__ailang_trap_msg && !__ailang_abnormal_exit) return;"
    )
    self._output.append(
        "    if (disabled && !forced && !__ailang_trap_msg && !__ailang_abnormal_exit) return;"
    )
    self._output.append('    fprintf(stderr, "\\n=== AILang memory report ===\\n");')
    self._output.append("#if AILANG_TRACK_ALLOCATIONS")
    self._output.append(
        '    fprintf(stderr, "  total allocated: %zu bytes\\n", '
        "__ailang_total_allocated);"
    )
    self._output.append(
        '    fprintf(stderr, "  total freed:     %zu bytes\\n", '
        "__ailang_total_freed);"
    )
    self._output.append("#else")
    self._output.append('    fprintf(stderr, "  total allocated: disabled\\n");')
    self._output.append('    fprintf(stderr, "  total freed:     disabled\\n");')
    self._output.append("#endif")
    # Three distinct outcomes:
    #   1. trap-aborted exit: bytes ARE leaked (program never freed
    #      them) but the CAUSE is the trap killing the process
    #      before cleanup ran, not a bug in the tracker. Label it
    #      so users know to look at the trap, not at their dealloc
    #      logic.
    #   2. abnormal/interrupted exit: live bytes may exist because
    #      scope cleanup did not get a normal return path.
    #   3. normal exit with live > 0: real leak from the program.
    #   4. clean exit: live == 0.
    self._output.append("    if (__ailang_trap_msg) {")
    self._output.append("        if (live > 0) {")
    self._output.append(
        '            fprintf(stderr, "  live at exit:    %zu bytes  '
        '** LEAKED (trap-aborted) **\\n", live);'
    )
    self._output.append("        } else {")
    self._output.append(
        '            fprintf(stderr, "  live at exit:    0 bytes '
        '(no allocations were live when trap fired)\\n");'
    )
    self._output.append("        }")
    self._output.append(
        '        fprintf(stderr, "  cause: safety trap fired (%s),\\n'
        "   so the process was killed before any subsequent dealloc /"
        " cleanup\\n"
        '   code in your program could run.\\n", __ailang_trap_msg);'
    )
    self._output.append("    } else if (__ailang_abnormal_exit) {")
    self._output.append("        if (live > 0) {")
    self._output.append(
        '            fprintf(stderr, "  live at exit:    %zu bytes  '
        '** CLEANUP INTERRUPTED **\\n", live);'
    )
    self._output.append("        } else {")
    self._output.append(
        '            fprintf(stderr, "  live at exit:    0 bytes '
        '(no allocations were live when interruption occurred)\\n");'
    )
    self._output.append("        }")
    self._output.append(
        '        fprintf(stderr, "  cause: process interrupted before normal '
        'AILang cleanup completed (code=%d).\\n", (int)__ailang_abnormal_code);'
    )
    self._output.append(
        '        fprintf(stderr, "  classification: not a completed-exit leak; '
        'rerun without interruption for leak validation.\\n");'
    )
    self._output.append("    } else if (live > 0) {")
    self._output.append(
        '        fprintf(stderr, "  live at exit:    %zu bytes  '
        '** POSSIBLE LEAK **\\n", live);'
    )
    self._output.append(
        '        fprintf(stderr, "  (long-lived state and intentional'
        " caches also show here;\\n"
        "   set AILANG_LEAK_REPORT=0 to silence,\\n"
        '   AILANG_LEAK_REPORT=1 to print this report even when 0)\\n");'
    )
    self._output.append("    } else {")
    self._output.append(
        '        fprintf(stderr, "  live at exit:    0 bytes (clean)\\n");'
    )
    self._output.append("    }")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")

    emit_safety_tail_helpers(self)


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = emit_safety_helpers
