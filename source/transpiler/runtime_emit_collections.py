"""Runtime emitter helpers for runtime emit collections."""

from __future__ import annotations

__all__ = ["emit_runtime_dict", "emit_runtime_dynamic_array", "emit_runtime_str_array"]


def emit_runtime_dict(self) -> None:
    """Emit dictionary/hashmap runtime helpers."""
    if "dict" not in self._needs.helpers:
        return

    self._output.append("/* Dictionary (hash map) implementation */")
    self._output.append("#define DICT_INITIAL_CAP 16")
    self._output.append("#define DICT_LOAD_FACTOR 0.75")
    self._output.append("")
    self._output.append("struct ailang_dict_entry {")
    self._output.append("    const char *key;")
    self._output.append("    int64_t value;")
    self._output.append("    int occupied;")
    self._output.append("};")
    self._output.append("")
    self._output.append("struct ailang_dict {")
    self._output.append("    ailang_dict_entry *entries;")
    self._output.append("    int64_t capacity;")
    self._output.append("    int64_t size;")
    self._output.append("    int entries_owned;")
    self._output.append("    int dict_owned;")
    self._output.append("};")
    self._output.append("")
    self._output.append("static uint64_t dict_hash(const char *key) {")
    self._output.append("    if (key[0] != '\\0' && key[1] == '\\0') {")
    self._output.append("        return (uint64_t)(unsigned char)key[0];")
    self._output.append("    }")
    self._output.append("    uint64_t hash = 5381;")
    self._output.append(
        "    while (*key) hash = ((hash << 5) + hash) + (uint64_t)*key++;"
    )
    self._output.append("    return hash;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static inline uint64_t dict_index(uint64_t hash, int64_t capacity) {"
    )
    self._output.append("    return hash & ((uint64_t)capacity - 1u);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static inline int dict_key_equal(const char *left, const char *right) {"
    )
    self._output.append(
        "    return left == right || __ailang_strcmp_raw(left, right) == 0;"
    )
    self._output.append("}")
    self._output.append("")
    self._output.append("static ailang_dict *dict_new(void) {")
    self._output.append(
        "    ailang_dict *d = (ailang_dict*)ailang_safe_malloc(sizeof(ailang_dict));"
    )
    self._output.append("    d->capacity = DICT_INITIAL_CAP;")
    self._output.append("    d->size = 0;")
    self._output.append("    d->entries_owned = 1;")
    self._output.append("    d->dict_owned = 1;")
    self._output.append(
        "    d->entries = (ailang_dict_entry*)ailang_safe_calloc("
        "(size_t)d->capacity, sizeof(ailang_dict_entry));"
    )
    self._output.append("    return d;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static void dict_set(ailang_dict *d, const char *key, int64_t val) {"
    )
    self._output.append(
        "    if (d->size >= (int64_t)(d->capacity * DICT_LOAD_FACTOR)) {"
    )
    self._output.append("        /* Resize */")
    self._output.append("        int64_t old_cap = d->capacity;")
    self._output.append("        ailang_dict_entry *old = d->entries;")
    self._output.append("        int old_owned = d->entries_owned;")
    self._output.append("        d->capacity *= 2;")
    self._output.append(
        "        d->entries = (ailang_dict_entry*)ailang_safe_calloc("
        "(size_t)d->capacity, sizeof(ailang_dict_entry));"
    )
    self._output.append("        d->size = 0;")
    self._output.append("        d->entries_owned = 1;")
    self._output.append("        for (int64_t i = 0; i < old_cap; i++) {")
    self._output.append(
        "            if (old[i].occupied) dict_set(d, old[i].key, old[i].value);"
    )
    self._output.append("        }")
    self._output.append("        if (old_owned) ailang_safe_free(old);")
    self._output.append("    }")
    self._output.append("    uint64_t idx = dict_index(dict_hash(key), d->capacity);")
    self._output.append("    while (d->entries[idx].occupied) {")
    self._output.append("        if (dict_key_equal(d->entries[idx].key, key)) {")
    self._output.append("            d->entries[idx].value = val;")
    self._output.append("            return;")
    self._output.append("        }")
    self._output.append("        idx = (idx + 1) % (uint64_t)d->capacity;")
    self._output.append("    }")
    self._output.append("    d->entries[idx].key = key;")
    self._output.append("    d->entries[idx].value = val;")
    self._output.append("    d->entries[idx].occupied = 1;")
    self._output.append("    d->size++;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t dict_get(ailang_dict *d, const char *key) {")
    self._output.append("    uint64_t idx = dict_index(dict_hash(key), d->capacity);")
    self._output.append("    int64_t start = (int64_t)idx;")
    self._output.append("    while (d->entries[idx].occupied) {")
    self._output.append("        if (dict_key_equal(d->entries[idx].key, key)) {")
    self._output.append("            return d->entries[idx].value;")
    self._output.append("        }")
    self._output.append("        idx = (idx + 1) % (uint64_t)d->capacity;")
    self._output.append("        if ((int64_t)idx == start) break;")
    self._output.append("    }")
    self._output.append("    return 0; /* Key not found */")
    self._output.append("}")
    self._output.append("")

    # Dict introspection functions (dict_size, dict_key_at, dict_value_at, dict_remove)
    self._output.append(
        "static int64_t dict_size_fn(ailang_dict *d) { return d->size; }"
    )
    self._output.append("")
    self._output.append("static const char *dict_key_at(ailang_dict *d, int64_t idx) {")
    self._output.append("    int64_t count = 0;")
    self._output.append("    for (int64_t i = 0; i < d->capacity; i++) {")
    self._output.append("        if (d->entries[i].occupied) {")
    self._output.append("            if (count == idx) return d->entries[i].key;")
    self._output.append("            count++;")
    self._output.append("        }")
    self._output.append("    }")
    self._output.append('    return "";  /* out of bounds */')
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t dict_value_at(ailang_dict *d, int64_t idx) {")
    self._output.append("    int64_t count = 0;")
    self._output.append("    for (int64_t i = 0; i < d->capacity; i++) {")
    self._output.append("        if (d->entries[i].occupied) {")
    self._output.append("            if (count == idx) return d->entries[i].value;")
    self._output.append("            count++;")
    self._output.append("        }")
    self._output.append("    }")
    self._output.append("    return 0;  /* out of bounds */")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t dict_remove_fn(ailang_dict *d, const char *key) {"
    )
    self._output.append("    uint64_t idx = dict_index(dict_hash(key), d->capacity);")
    self._output.append("    int64_t start = (int64_t)idx;")
    self._output.append("    while (d->entries[idx].occupied) {")
    self._output.append("        if (dict_key_equal(d->entries[idx].key, key)) {")
    self._output.append("            d->entries[idx].occupied = 0;")
    self._output.append("            d->entries[idx].key = NULL;")
    self._output.append("            d->size--;")
    self._output.append("            return 1;  /* removed */")
    self._output.append("        }")
    self._output.append("        idx = (idx + 1) % (uint64_t)d->capacity;")
    self._output.append("        if ((int64_t)idx == start) break;")
    self._output.append("    }")
    self._output.append("    return 0;  /* not found */")
    self._output.append("}")
    self._output.append("")
    # dict_has -- returns 1 if key is present, 0 otherwise.
    # Listed in builtins/diagnostics but had no implementation; user
    # programs failed at link with `implicit declaration of dict_has`.
    self._output.append("static int64_t dict_has_fn(ailang_dict *d, const char *key) {")
    self._output.append("    uint64_t idx = dict_index(dict_hash(key), d->capacity);")
    self._output.append("    int64_t start = (int64_t)idx;")
    self._output.append("    while (d->entries[idx].occupied) {")
    self._output.append(
        "        if (dict_key_equal(d->entries[idx].key, key)) return 1;"
    )
    self._output.append("        idx = (idx + 1) % (uint64_t)d->capacity;")
    self._output.append("        if ((int64_t)idx == start) break;")
    self._output.append("    }")
    self._output.append("    return 0;")
    self._output.append("}")
    self._output.append("")
    # dict_get_type_fn -- returns a static string describing the
    # value type. AILang dict values are stored as int64; we don't
    # currently track per-entry type tags, so the answer is "int"
    # for present keys and "missing" otherwise. Static strings --
    # never allocated, never freed.
    self._output.append(
        "static const char *dict_get_type_fn(" "ailang_dict *d, const char *key) {"
    )
    self._output.append('    return dict_has_fn(d, key) ? "int" : "missing";')
    self._output.append("}")
    self._output.append("")
    # dict_destroy_fn -- releases the struct and the entries array
    # (entries.key pointers are caller-owned literals/long-lived
    # strings, so we never strdup them; nothing to free per-entry).
    # Called from scope-exit auto-cleanup so a `d = {...}` literal
    # local doesn't leak the 24-byte struct + ~capacity*entry array
    # at function return.
    self._output.append("static void dict_destroy_fn(ailang_dict *d) {")
    self._output.append("    if (!d) return;")
    self._output.append("    if (d->entries_owned) ailang_safe_free(d->entries);")
    self._output.append("    if (d->dict_owned) ailang_safe_free(d);")
    self._output.append("}")
    self._output.append("")


def emit_runtime_dynamic_array(self) -> None:
    """Emit dynamic array runtime helpers."""
    if "dynamic_array" not in self._needs.helpers:
        return

    self._output.append("/* Dynamic array implementation */")
    # Typedef already emitted in _emit_dynamic_collection_typedefs
    # (which runs before user type definitions so class fields can
    # reference the struct). Skip re-emission here.
    self._output.append(
        "AILANG_UNUSED static inline ailang_dyn_array array_new(int64_t cap) {"
    )
    self._output.append("    ailang_dyn_array arr;")
    self._output.append("    arr.capacity = cap > 0 ? cap : 4;")
    self._output.append("    arr.length = 0;")
    self._output.append(
        "    arr.data = (int64_t*)ailang_safe_malloc("
        "(size_t)arr.capacity * sizeof(int64_t));"
    )
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static inline ailang_dyn_array array_push(ailang_dyn_array arr, int64_t val) {"
    )
    self._output.append("    if (arr.length >= arr.capacity) {")
    self._output.append("        arr.capacity = arr.capacity ? arr.capacity * 2 : 4;")
    self._output.append(
        "        arr.data = (int64_t*)ailang_safe_realloc(arr.data, "
        "(size_t)arr.capacity * sizeof(int64_t));"
    )
    self._output.append("    }")
    self._output.append("    arr.data[arr.length++] = val;")
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_pop(ailang_dyn_array *arr) {")
    self._output.append("    if (arr->length <= 0) return 0;")
    self._output.append("    return arr->data[--arr->length];")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static inline int64_t array_get(ailang_dyn_array arr, int64_t idx) {"
    )
    self._output.append("    if (idx < 0 || idx >= arr.length) return 0;")
    self._output.append("    return arr.data[idx];")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "AILANG_UNUSED static inline ailang_dyn_array array_set(ailang_dyn_array arr, int64_t idx, int64_t val) {"
    )
    self._output.append("    if (idx >= 0 && idx < arr.length) arr.data[idx] = val;")
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_len(ailang_dyn_array arr) {")
    self._output.append("    return arr.length;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t array_cap(ailang_dyn_array arr) {")
    self._output.append("    return arr.capacity;")
    self._output.append("}")
    self._output.append("")


def emit_runtime_str_array(self) -> None:
    """Emit string dynamic array runtime helpers."""
    if "str_array" not in self._needs.helpers:
        return

    self._output.append("/* String dynamic array implementation */")
    # Typedef already emitted in _emit_dynamic_collection_typedefs.
    self._output.append("static ailang_str_array str_array_new_fn(int64_t cap) {")
    self._output.append("    ailang_str_array arr;")
    self._output.append("    arr.capacity = cap > 0 ? cap : 4;")
    self._output.append("    arr.length = 0;")
    self._output.append(
        "    arr.data = (const char**)ailang_safe_malloc("
        "(size_t)arr.capacity * sizeof(const char*));"
    )
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t str_array_len_fn(ailang_str_array arr) {")
    self._output.append("    return arr.length;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static ailang_str_array str_array_push_fn("
        "ailang_str_array arr, const char *s) {"
    )
    self._output.append("    if (arr.length >= arr.capacity) {")
    self._output.append("        arr.capacity = arr.capacity ? arr.capacity * 2 : 4;")
    self._output.append(
        "        arr.data = (const char**)ailang_safe_realloc(arr.data, "
        "(size_t)arr.capacity * sizeof(const char*));"
    )
    self._output.append("    }")
    self._output.append("    arr.data[arr.length++] = s;")
    self._output.append("    return arr;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static const char* str_array_get_fn(ailang_str_array arr, int64_t i) {"
    )
    self._output.append('    if (i < 0 || i >= arr.length) return "";')
    self._output.append("    return arr.data[i];")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static void str_array_set_fn("
        "ailang_str_array arr, int64_t i, const char *s) {"
    )
    self._output.append("    if (i >= 0 && i < arr.length) arr.data[i] = s;")
    self._output.append("}")
    self._output.append("")
    # Two-pass O(n) join: sum lengths once, allocate once, copy once.
    # Replaces the O(n^2) `s = s + ...` loop pattern. See
    # perf_jit_driver/microbench/string_concat_loop.* for the empirical
    # proof of the original problem.
    self._output.append(
        "static const char* str_array_join_fn("
        "ailang_str_array arr, const char *sep) {"
    )
    # Always allocate, never return a borrowed literal. Previously the
    # `arr.length == 0` and `!result` fast paths returned `""` (.rodata),
    # but the contract for callers (incl. dealloc + scope-exit cleanup)
    # treats this result as owned heap. Returning a literal triggers
    # STATUS_HEAP_CORRUPTION on Windows when caller frees. Bug C
    # workaround at the runtime layer; see OPEN_QUESTIONS.md
    # 30-04-2026 / 15:12:00.
    self._output.append("    if (arr.length == 0) {")
    self._output.append("        char *empty = (char*)ailang_safe_malloc(1);")
    self._output.append("        empty[0] = '\\0';")
    self._output.append("        return empty;")
    self._output.append("    }")
    self._output.append("    size_t sep_len = sep ? __ailang_strlen_raw(sep) : 0;")
    self._output.append("    size_t total = 0;")
    self._output.append("    for (int64_t i = 0; i < arr.length; i++) {")
    self._output.append(
        "        if (arr.data[i]) total += __ailang_strlen_raw(arr.data[i]);"
    )
    self._output.append("    }")
    self._output.append(
        "    if (arr.length > 1) total += sep_len * (size_t)(arr.length - 1);"
    )
    self._output.append("    char *result = (char*)ailang_safe_malloc(total + 1);")
    # ailang_safe_malloc traps on OOM (calls __ailang_safety_trap), so
    # `!result` is unreachable -- but keep a defensive empty heap return
    # in case the implementation ever changes. Never return literal.
    self._output.append("    if (!result) {")
    self._output.append("        char *empty = (char*)ailang_safe_malloc(1);")
    self._output.append("        empty[0] = '\\0';")
    self._output.append("        return empty;")
    self._output.append("    }")
    self._output.append("    char *p = result;")
    self._output.append("    for (int64_t i = 0; i < arr.length; i++) {")
    self._output.append("        if (i > 0 && sep_len > 0) {")
    self._output.append("            __ailang_memcpy_raw(p, sep, sep_len);")
    self._output.append("            p += sep_len;")
    self._output.append("        }")
    self._output.append("        if (arr.data[i]) {")
    self._output.append("            size_t len = __ailang_strlen_raw(arr.data[i]);")
    self._output.append("            __ailang_memcpy_raw(p, arr.data[i], len);")
    self._output.append("            p += len;")
    self._output.append("        }")
    self._output.append("    }")
    self._output.append("    *p = '\\0';")
    self._output.append("    return result;")
    self._output.append("}")
    self._output.append("")


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = (
    emit_runtime_dict,
    emit_runtime_dynamic_array,
    emit_runtime_str_array,
)
