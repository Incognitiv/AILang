"""Runtime emitter helpers split from string-runtime auxiliary helpers."""

from __future__ import annotations

__all__ = [
    "emit_arena_helper",
    "emit_dynamic_array_helpers",
    "emit_input_helper",
]


def emit_arena_helper(self) -> None:
    """Emit arena allocator -- zero-overhead bump allocator with O(1) bulk free."""
    self._output.append("/* Arena allocator: bump allocation with O(1) reset */")
    self._output.append("typedef struct {")
    self._output.append("    char *base;")
    self._output.append("    char *current;")
    self._output.append("    char *end;")
    self._output.append("} AilangArena;")
    self._output.append("")
    # arena_create
    self._output.append("static void *arena_create(int64_t size) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (size < 0) {")
    self._output.append(
        '        fprintf(stderr, "Error: arena_create() size must be non-negative\\n");'
    )
    self._output.append('        __ailang_safety_trap("arena_create: negative size");')
    self._output.append("    }")
    self._output.append(
        "    if ((uint64_t)size > (uint64_t)SIZE_MAX - (uint64_t)sizeof(AilangArena)) {"
    )
    self._output.append(
        '        fprintf(stderr, "Error: arena_create() size overflow\\n");'
    )
    self._output.append('        __ailang_safety_trap("arena_create: size overflow");')
    self._output.append("    }")
    self._output.append("    size_t payload = (size_t)size;")
    self._output.append("    size_t total = sizeof(AilangArena) + payload;")
    self._output.append("    char *block = (char *)ailang_safe_malloc(total);")
    self._output.append("    if (!block) {")
    self._output.append(
        '        fprintf(stderr, "Error: Out of memory creating arena!\\n");'
    )
    self._output.append('        __ailang_safety_trap("out of memory creating arena");')
    self._output.append("    }")
    self._output.append("    AilangArena *arena = (AilangArena *)block;")
    self._output.append("    arena->base = block + sizeof(AilangArena);")
    self._output.append("    arena->current = arena->base;")
    self._output.append("    arena->end = block + total;")
    self._output.append("    return (void *)arena;")
    self._output.append("#else")
    self._output.append("    (void)size;")
    self._output.append("    return NULL;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    # arena_alloc
    self._output.append("static void *arena_alloc(void *a, int64_t size) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (a == NULL) {")
    self._output.append(
        '        fprintf(stderr, "Error: arena_alloc() called with null arena\\n");'
    )
    self._output.append('        __ailang_safety_trap("arena_alloc: null arena");')
    self._output.append("    }")
    self._output.append("    if (size < 0) {")
    self._output.append(
        '        fprintf(stderr, "Error: arena_alloc() size must be non-negative\\n");'
    )
    self._output.append('        __ailang_safety_trap("arena_alloc: negative size");')
    self._output.append("    }")
    self._output.append("    AilangArena *arena = (AilangArena *)a;")
    self._output.append("    size_t req = (size_t)size;")
    self._output.append("    size_t remaining = (size_t)(arena->end - arena->current);")
    self._output.append("    if (req > remaining) {")
    self._output.append(
        '        fprintf(stderr, "Error: Arena allocation overflow!\\n");'
    )
    self._output.append('        __ailang_safety_trap("arena allocation overflow");')
    self._output.append("    }")
    self._output.append("    void *ptr = (void *)arena->current;")
    self._output.append("    arena->current += req;")
    self._output.append("    return ptr;")
    self._output.append("#else")
    self._output.append("    (void)a; (void)size;")
    self._output.append("    return NULL;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    # arena_reset
    self._output.append("static void arena_reset(void *a) {")
    self._output.append("    AilangArena *arena = (AilangArena *)a;")
    self._output.append("    arena->current = arena->base;")
    self._output.append("}")
    self._output.append("")
    # arena_destroy. Three steps:
    #   1. Snapshot [base, end) into the per-thread graveyard so
    #      auto-cleanup of arena-resident locals can still
    #      recognize them as no-op.
    #   2. Clear the TLS pointer if it pointed at this arena --
    #      otherwise the next safe_free reads freed memory for
    #      the range check.
    #   3. Free the arena buffer through the tracker.
    self._output.append("static void arena_destroy(void *a) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (a) {")
    self._output.append("        AilangArena *arena = (AilangArena *)a;")
    self._output.append("        int idx = __ailang_arena_graveyard_idx;")
    self._output.append("        __ailang_arena_graveyard[idx].base = arena->base;")
    self._output.append("        __ailang_arena_graveyard[idx].end = arena->end;")
    self._output.append(
        "        __ailang_arena_graveyard_idx ="
        " (idx + 1) % AILANG_ARENA_GRAVEYARD_SIZE;"
    )
    # Trip the graveyard-filled flag so safe_free's short-circuit
    # kicks in for subsequent frees on this thread. Once set it
    # stays set -- the cost of an 8-iter scan once destruction has
    # happened is acceptable, the cost on every server with a
    # never-destroyed request arena is not.
    self._output.append("        __ailang_arena_graveyard_filled = 1;")
    self._output.append("    }")
    self._output.append("    if (__ailang_request_arena == a) {")
    self._output.append("        __ailang_request_arena = NULL;")
    self._output.append("    }")
    self._output.append("    ailang_safe_free(a);")
    self._output.append("#else")
    self._output.append("    (void)a;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    # arena_used
    self._output.append("static int64_t arena_used(void *a) {")
    self._output.append("    AilangArena *arena = (AilangArena *)a;")
    self._output.append("    return (int64_t)(arena->current - arena->base);")
    self._output.append("}")
    self._output.append("")
    # arena_remaining
    self._output.append("static int64_t arena_remaining(void *a) {")
    self._output.append("    AilangArena *arena = (AilangArena *)a;")
    self._output.append("    return (int64_t)(arena->end - arena->current);")
    self._output.append("}")
    self._output.append("")


def emit_input_helper(self) -> None:
    """Emit input helper function."""
    self._output.append("static char *read_stdin(void) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    size_t cap = 4096;")
    self._output.append("    size_t len = 0;")
    self._output.append("    char *buf = (char *)ailang_safe_malloc(cap);")
    self._output.append("    if (!buf) return NULL;")
    self._output.append("    int ch;")
    self._output.append("    while ((ch = fgetc(stdin)) != EOF) {")
    self._output.append("        if (len + 1 >= cap) {")
    self._output.append("            if (cap > (size_t)AILANG_MAX_ALLOC_SIZE / 2U) {")
    self._output.append("                ailang_safe_free(buf);")
    self._output.append(
        '                __ailang_safety_trap("read_stdin: input exceeds allocation cap");'
    )
    self._output.append("            }")
    self._output.append("            cap *= 2U;")
    self._output.append("            buf = (char *)ailang_safe_realloc(buf, cap);")
    self._output.append("        }")
    self._output.append("        buf[len++] = (char)ch;")
    self._output.append("    }")
    self._output.append("    buf[len] = '\\0';")
    self._output.append("    return buf;")
    self._output.append("#else")
    self._output.append("    return NULL;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")

    self._output.append("static char *input(const char *prompt) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append('    if (prompt && prompt[0]) printf("%s", prompt);')
    self._output.append("    char *buf = (char *)ailang_safe_malloc(1024);")
    self._output.append("    if (!buf) return NULL;")
    self._output.append("    if (fgets(buf, 1024, stdin)) {")
    self._output.append("        size_t len = strlen(buf);")
    self._output.append(
        "        if (len > 0 && buf[len-1] == '\\n') buf[len-1] = '\\0';"
    )
    self._output.append("        return buf;")
    self._output.append("    }")
    # input() returned NULL (EOF/error). buf was tracked-alloc'd, so
    # use the tracked free to keep counters symmetric.
    self._output.append("    ailang_safe_free(buf);")
    self._output.append("    return NULL;")
    self._output.append("#else")
    self._output.append("    (void)prompt;")
    self._output.append("    return NULL;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")


def emit_dynamic_array_helpers(self) -> None:
    """Emit dynamic array helper functions."""
    self._output.append("/* Dynamic array implementation */")
    self._output.append("typedef struct {")
    self._output.append("    int64_t *data;")
    self._output.append("    int64_t length;")
    self._output.append("    int64_t capacity;")
    self._output.append("} dyn_array;")
    self._output.append("")
    self._output.append("static dyn_array array_new(int64_t cap) {")
    self._output.append("    dyn_array arr = {NULL, 0, cap};")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append(
        "    arr.data = (int64_t *)ailang_safe_malloc((size_t)cap * sizeof(int64_t));"
    )
    self._output.append("#endif")
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static void array_push(dyn_array *arr, int64_t val) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (arr->length >= arr->capacity) {")
    self._output.append(
        "        arr->capacity = arr->capacity ? arr->capacity * 2 : 8;"
    )
    self._output.append(
        "        arr->data = (int64_t *)realloc(arr->data, "
        "(size_t)arr->capacity * sizeof(int64_t));"
    )
    self._output.append("    }")
    self._output.append("    arr->data[arr->length++] = val;")
    self._output.append("#else")
    self._output.append("    (void)arr; (void)val;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_pop(dyn_array *arr) {")
    self._output.append("    if (arr->length > 0) return arr->data[--arr->length];")
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_get(dyn_array *arr, int64_t idx) {")
    self._output.append("    if (idx >= 0 && idx < arr->length) return arr->data[idx];")
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static void array_set(dyn_array *arr, int64_t idx, int64_t val) {"
    )
    self._output.append("    if (idx >= 0 && idx < arr->length) arr->data[idx] = val;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_len(dyn_array *arr) {")
    self._output.append("    return arr->length;")
    self._output.append("}")
    self._output.append("")
