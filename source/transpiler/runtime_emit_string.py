"""Runtime emitter helpers for runtime emit string."""

from __future__ import annotations

from transpiler.runtime_emit_string_writers import emit_typed_writer_helpers

from .runtime_emit_baseconv import emit_base_conversion_helpers

__all__ = [
    "emit_runtime_string",
    "emit_split_ints_helper",
    "emit_split_helper",
    "emit_parse_int_helper",
    "emit_base_conversion_helpers",
]


def emit_runtime_string(self) -> None:
    """Emit string-related runtime helpers."""
    # String length - freestanding compatible
    if "strlen" in self._needs.helpers:
        self._output.append("static int64_t ailang_strlen(const char *s) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    return (int64_t)strlen(s);")
        self._output.append("#else")
        self._output.append("    int64_t len = 0;")
        self._output.append("    while (s[len]) len++;")
        self._output.append("    return len;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    if "i64_decimal_len" in self._needs.helpers:
        self._output.append(
            "AILANG_UNUSED static int64_t ailang_i64_decimal_len(int64_t n) {"
        )
        self._output.append("    int64_t len = 0;")
        self._output.append("    uint64_t u;")
        self._output.append("    if (n < 0) {")
        self._output.append("        len = 1;")
        self._output.append(
            "        u = (uint64_t)(-(n + 1)) + 1U; /* safe vs INT64_MIN */"
        )
        self._output.append("    } else {")
        self._output.append("        u = (uint64_t)n;")
        self._output.append("    }")
        self._output.append("    do {")
        self._output.append("        len++;")
        self._output.append("        u /= 10U;")
        self._output.append("    } while (u != 0U);")
        self._output.append("    return len;")
        self._output.append("}")
        self._output.append("")

    if "streq_lit" in self._needs.helpers:
        self._output.append(
            "static int64_t ailang_streq_lit(const char *s, const char *lit, "
            "int64_t lit_len) {"
        )
        self._output.append("    if (!s || !lit || lit_len < 0) return 0;")
        self._output.append("    for (int64_t i = 0; i < lit_len; i++) {")
        self._output.append(
            "        if ((unsigned char)s[i] != (unsigned char)lit[i]) return 0;"
        )
        self._output.append("        if (s[i] == '\\0') return 0;")
        self._output.append("    }")
        self._output.append("    return s[lit_len] == '\\0' ? 1 : 0;")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "static int64_t ailang_streq_slice_lit(const char *s, int64_t start, "
            "int64_t len, const char *lit, int64_t lit_len) {"
        )
        self._output.append("    if (!s || !lit || lit_len < 0) return 0;")
        self._output.append("    int64_t slen = (int64_t)strlen(s);")
        self._output.append("    if (start < 0) start = 0;")
        self._output.append("    if (start >= slen) return lit_len == 0 ? 1 : 0;")
        self._output.append(
            "    if (len < 0 || start + len > slen) len = slen - start;"
        )
        self._output.append("    if (len != lit_len) return 0;")
        self._output.append("    for (int64_t i = 0; i < lit_len; i++) {")
        self._output.append(
            "        if ((unsigned char)s[start + i] != (unsigned char)lit[i]) return 0;"
        )
        self._output.append("    }")
        self._output.append("    return 1;")
        self._output.append("}")
        self._output.append("")

    # Safe character access with bounds checking
    if "char_at" in self._needs.helpers:
        self._output.append(
            "static int64_t char_at(const char *s, int64_t i, int64_t slen) {"
        )
        self._output.append(
            "    int64_t len = (slen >= 0) ? slen : (int64_t)strlen(s);"
        )
        self._output.append("    if (i < 0 || i >= len) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append(
            '        fprintf(stderr, "Error: string index %lld out of bounds '
            '[0, %lld)\\n", (long long)i, (long long)len);'
        )
        self._output.append("#endif")
        self._output.append(
            '        __ailang_safety_trap("string index out of bounds");'
        )
        self._output.append("    }")
        self._output.append("    return (int64_t)(unsigned char)s[i];")
        self._output.append("}")
        self._output.append("")

    # Unsafe (fast) character access - NO bounds check
    if "unsafe_char_at" in self._needs.helpers:
        self._output.append("static int64_t unsafe_char_at(const char *s, int64_t i) {")
        self._output.append("    return (int64_t)(unsigned char)s[i];")
        self._output.append("}")
        self._output.append("")

    # String concatenation - freestanding compatible
    if "strcat" in self._needs.helpers:
        self._output.append(
            "static char *ailang_strcat(const char *a, const char *b) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t len_a = strlen(a);")
        self._output.append("    size_t len_b = strlen(b);")
        self._output.append(
            "    char *result = (char *)ailang_request_alloc(len_a + len_b + 1);"
        )
        self._output.append("    if (result == NULL) return NULL;")
        self._output.append("    memcpy(result, a, len_a);")
        self._output.append("    memcpy(result + len_a, b, len_b + 1);")
        self._output.append("    return result;")
        self._output.append("#else")
        self._output.append("    /* Freestanding: no malloc, return NULL */")
        self._output.append("    (void)a; (void)b;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        # Consuming strcat: emitted by codegen for chained concats like
        # `a + b + c`, where intermediate results are owned temps that
        # would otherwise leak. Mirrors the LLVM backend's temp_strings
        # discipline. `free_a`/`free_b` are 0/1 flags chosen at compile
        # time based on whether each operand is itself an owned temp.
        self._output.append(
            "AILANG_UNUSED static char *ailang_strcat_consuming("
            "const char *a, const char *b, int free_a, int free_b) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    char *result = ailang_strcat(a, b);")
        self._output.append("    if (free_a) ailang_safe_free((void *)a);")
        self._output.append("    if (free_b) ailang_safe_free((void *)b);")
        self._output.append("    return result;")
        self._output.append("#else")
        self._output.append("    (void)a; (void)b; (void)free_a; (void)free_b;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "AILANG_UNUSED static char *ailang_strcat_lit_i64("
            "const char *prefix, size_t prefix_len, int64_t n) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    char tmp[24];")
        self._output.append("    int digits = 0;")
        self._output.append("    int negative = 0;")
        self._output.append("    uint64_t u;")
        self._output.append("    if (n < 0) {")
        self._output.append("        negative = 1;")
        self._output.append(
            "        u = (uint64_t)(-(n + 1)) + 1U; /* safe vs INT64_MIN */"
        )
        self._output.append("    } else {")
        self._output.append("        u = (uint64_t)n;")
        self._output.append("    }")
        self._output.append("    do {")
        self._output.append("        tmp[digits++] = (char)('0' + (u % 10U));")
        self._output.append("        u /= 10U;")
        self._output.append("    } while (u != 0U);")
        self._output.append(
            "    size_t total = prefix_len + (size_t)digits + " "(negative ? 1u : 0u);"
        )
        self._output.append(
            "    char *result = (char *)ailang_request_alloc(total + 1u);"
        )
        self._output.append("    if (!result) return NULL;")
        self._output.append("    memcpy(result, prefix, prefix_len);")
        self._output.append("    char *p = result + prefix_len;")
        self._output.append("    if (negative) *p++ = '-';")
        self._output.append("    while (digits > 0) *p++ = tmp[--digits];")
        self._output.append("    *p = '\\0';")
        self._output.append("    return result;")
        self._output.append("#else")
        self._output.append("    (void)prefix; (void)prefix_len; (void)n;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        # Single-pass concat for `+`-chains. Replaces O(n^2) chained
        # strcat with O(n + total_length): one strlen per operand,
        # one alloc, one memcpy per operand. The transpiler emits
        # a call to this for any 3+-deep `+` chain of strings.
        # Saved ~12% CPU in adapt_serve's /api/adapt/status response.
        self._output.append("#define AILANG_STRCAT_N_MAX 64")
        self._output.append(
            "AILANG_UNUSED static char *ailang_strcat_n("
            "int count, const char *const *parts, const int *owned,"
            " const size_t *precomp_lens) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append('    if (count <= 0) return ailang_strcat("", "");')
        self._output.append("    size_t lens[AILANG_STRCAT_N_MAX];")
        self._output.append("    if (count > AILANG_STRCAT_N_MAX) {")
        self._output.append(
            '        __ailang_safety_trap("strcat_n: too many operands");'
        )
        self._output.append("    }")
        self._output.append("    size_t total = 0;")
        self._output.append("    for (int i = 0; i < count; i++) {")
        self._output.append("        size_t pl = precomp_lens[i];")
        self._output.append(
            "        lens[i] = (pl == (size_t)-1) ? strlen(parts[i]) : pl;"
        )
        self._output.append("        total += lens[i];")
        self._output.append("    }")
        self._output.append(
            "    char *result = (char *)ailang_request_alloc(total + 1);"
        )
        self._output.append("    if (!result) return NULL;")
        self._output.append("    char *p = result;")
        self._output.append("    for (int i = 0; i < count; i++) {")
        self._output.append("        memcpy(p, parts[i], lens[i]);")
        self._output.append("        p += lens[i];")
        self._output.append("    }")
        self._output.append("    *p = '\\0';")
        self._output.append("    for (int i = 0; i < count; i++) {")
        self._output.append("        if (owned[i]) ailang_safe_free((void *)parts[i]);")
        self._output.append("    }")
        self._output.append("    return result;")
        self._output.append("#else")
        self._output.append("    (void)count; (void)parts; (void)owned;")
        self._output.append("    (void)precomp_lens;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # Int to string - freestanding compatible
    if "int_to_str" in self._needs.helpers:
        # Hand-rolled int-to-string. Avoids snprintf("%lld") which is
        # ~10x slower in glibc due to format-string parsing + locale
        # awareness. perf showed printf family at ~14% CPU when called
        # 6x per status response (vocabSize/eventCount/turnCount via
        # srv_jnum). Custom path: digits-in-reverse, then flip into
        # the request-arena buffer. Handles INT64_MIN via unsigned
        # negation trick (avoids UB on -INT64_MIN).
        self._output.append("static char *ailang_int_to_str(int64_t n) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    /* 20 digits max (INT64) + sign + NUL = 22 */")
        self._output.append("    char *buf = (char *)ailang_request_alloc(24);")
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    char tmp[24];")
        self._output.append("    int i = 0;")
        self._output.append("    int negative = 0;")
        self._output.append("    uint64_t u;")
        self._output.append("    if (n < 0) {")
        self._output.append("        negative = 1;")
        self._output.append(
            "        u = (uint64_t)(-(n + 1)) + 1U; /* safe vs INT64_MIN */"
        )
        self._output.append("    } else {")
        self._output.append("        u = (uint64_t)n;")
        self._output.append("    }")
        self._output.append("    do {")
        self._output.append("        tmp[i++] = (char)('0' + (u % 10U));")
        self._output.append("        u /= 10U;")
        self._output.append("    } while (u != 0U);")
        self._output.append("    int j = 0;")
        self._output.append("    if (negative) buf[j++] = '-';")
        self._output.append("    while (i > 0) buf[j++] = tmp[--i];")
        self._output.append("    buf[j] = '\\0';")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    /* Freestanding: no malloc, return NULL */")
        self._output.append("    (void)n;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # Typed stdout writers (P11): avoid generic printf format parsing in
    # known-shape print paths.
    if "print" in self._needs.helpers:
        emit_typed_writer_helpers(self)

    # Character from code point
    if "chr" in self._needs.helpers:
        self._output.append("static char *ailang_chr(int64_t code) {")
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    char *buf = (char *)ailang_request_alloc(2);")
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    buf[0] = (char)code;")
        self._output.append("    buf[1] = '\\0';")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    (void)code;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # Substring extraction
    if "substr" in self._needs.helpers:
        self._output.append(
            "static char *ailang_substr(const char *s, int64_t start, int64_t len) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    int64_t slen = (int64_t)strlen(s);")
        self._output.append("    if (start < 0) start = 0;")
        self._output.append("    if (start >= slen) {")
        self._output.append("        char *empty = (char *)ailang_request_alloc(1);")
        self._output.append("        if (empty) empty[0] = '\\0';")
        self._output.append("        return empty;")
        self._output.append("    }")
        self._output.append(
            "    if (len < 0 || start + len > slen) len = slen - start;"
        )
        self._output.append(
            "    char *buf = (char *)ailang_request_alloc((size_t)len + 1);"
        )
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    memcpy(buf, s + start, (size_t)len);")
        self._output.append("    buf[len] = '\\0';")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    (void)s; (void)start; (void)len;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # String concatenation (variadic via array)
    if "concat" in self._needs.helpers:
        self._output.append(
            "static char *ailang_concat2(const char *s1, const char *s2) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t len1 = strlen(s1);")
        self._output.append("    size_t len2 = strlen(s2);")
        self._output.append(
            "    char *buf = (char *)ailang_request_alloc(len1 + len2 + 1);"
        )
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    memcpy(buf, s1, len1);")
        self._output.append("    memcpy(buf + len1, s2, len2 + 1);")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    (void)s1; (void)s2;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "static char *ailang_concat3(const char *s1, const char *s2, const char *s3) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t len1 = strlen(s1);")
        self._output.append("    size_t len2 = strlen(s2);")
        self._output.append("    size_t len3 = strlen(s3);")
        self._output.append(
            "    char *buf = (char *)ailang_request_alloc(len1 + len2 + len3 + 1);"
        )
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    memcpy(buf, s1, len1);")
        self._output.append("    memcpy(buf + len1, s2, len2);")
        self._output.append("    memcpy(buf + len1 + len2, s3, len3 + 1);")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    (void)s1; (void)s2; (void)s3;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        self._output.append(
            "static char *ailang_concat4(const char *s1, const char *s2, const char *s3, const char *s4) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t len1 = strlen(s1);")
        self._output.append("    size_t len2 = strlen(s2);")
        self._output.append("    size_t len3 = strlen(s3);")
        self._output.append("    size_t len4 = strlen(s4);")
        self._output.append(
            "    char *buf = (char *)ailang_request_alloc(len1 + len2 + len3 + len4 + 1);"
        )
        self._output.append("    if (buf == NULL) return NULL;")
        self._output.append("    memcpy(buf, s1, len1);")
        self._output.append("    memcpy(buf + len1, s2, len2);")
        self._output.append("    memcpy(buf + len1 + len2, s3, len3);")
        self._output.append("    memcpy(buf + len1 + len2 + len3, s4, len4 + 1);")
        self._output.append("    return buf;")
        self._output.append("#else")
        self._output.append("    (void)s1; (void)s2; (void)s3; (void)s4;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # String index_of
    if "index_of" in self._needs.helpers:
        self._output.append(
            "static int64_t ailang_index_of(const char *haystack, const char *needle) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    const char *p = strstr(haystack, needle);")
        self._output.append("    return p ? (int64_t)(p - haystack) : -1LL;")
        self._output.append("#else")
        self._output.append("    (void)haystack; (void)needle;")
        self._output.append("    return -1LL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")
        # Variant taking a start offset -- needed for HTTP parsing
        # paths that scan the same buffer multiple times for
        # different headers. Using libc strstr (often Boyer-Moore-
        # Horspool-flavored) is dramatically faster than a hand-
        # rolled O(n*m) char_at loop. Saved ~5% CPU in adapt_serve.
        self._output.append(
            "static int64_t ailang_index_of_from("
            "const char *haystack, const char *needle, int64_t start) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    if (start <= 0) {")
        self._output.append("        const char *p = strstr(haystack, needle);")
        self._output.append("        return p ? (int64_t)(p - haystack) : -1LL;")
        self._output.append("    }")
        self._output.append("    size_t hlen = strlen(haystack);")
        self._output.append("    if ((size_t)start > hlen) return -1LL;")
        self._output.append("    const char *p = strstr(haystack + start, needle);")
        self._output.append("    return p ? (int64_t)(p - haystack) : -1LL;")
        self._output.append("#else")
        self._output.append("    (void)haystack; (void)needle; (void)start;")
        self._output.append("    return -1LL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # String startswith
    if "startswith" in self._needs.helpers:
        self._output.append(
            "static bool ailang_startswith(const char *s, const char *prefix) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t plen = strlen(prefix);")
        self._output.append("    return strncmp(s, prefix, plen) == 0;")
        self._output.append("#else")
        self._output.append("    (void)s; (void)prefix;")
        self._output.append("    return false;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # String endswith
    if "endswith" in self._needs.helpers:
        self._output.append(
            "static bool ailang_endswith(const char *s, const char *suffix) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    size_t slen = strlen(s);")
        self._output.append("    size_t suflen = strlen(suffix);")
        self._output.append("    if (suflen > slen) return false;")
        self._output.append("    return strcmp(s + slen - suflen, suffix) == 0;")
        self._output.append("#else")
        self._output.append("    (void)s; (void)suffix;")
        self._output.append("    return false;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # String replace
    if "str_replace" in self._needs.helpers:
        self._output.append(
            "static char *ailang_str_replace(const char *s, const char *old, const char *new_str) {"
        )
        self._output.append("#ifndef AILANG_FREESTANDING")
        self._output.append("    const char *p = strstr(s, old);")
        self._output.append("    if (!p) {")
        self._output.append(
            "        char *copy = (char *)ailang_request_alloc(strlen(s) + 1);"
        )
        self._output.append("        if (copy) strcpy(copy, s);")
        self._output.append("        return copy;")
        self._output.append("    }")
        self._output.append("    size_t prefix_len = (size_t)(p - s);")
        self._output.append("    size_t old_len = strlen(old);")
        self._output.append("    size_t new_len = strlen(new_str);")
        self._output.append("    size_t suffix_len = strlen(p + old_len);")
        self._output.append(
            "    char *result = (char *)ailang_request_alloc(prefix_len + new_len + suffix_len + 1);"
        )
        self._output.append("    if (result) {")
        self._output.append("        memcpy(result, s, prefix_len);")
        self._output.append("        memcpy(result + prefix_len, new_str, new_len);")
        self._output.append(
            "        strcpy(result + prefix_len + new_len, p + old_len);"
        )
        self._output.append("    }")
        self._output.append("    return result;")
        self._output.append("#else")
        self._output.append("    (void)s; (void)old; (void)new_str;")
        self._output.append("    return NULL;")
        self._output.append("#endif")
        self._output.append("}")
        self._output.append("")

    # Split string into array of integers (for parsing "1 2 3 4")
    if "split_ints" in self._needs.helpers:
        self._emit_split_ints_helper()

    # Split string into array of strings
    if "split" in self._needs.helpers:
        self._emit_split_helper()

    # Parse integer from string
    if "parse_int" in self._needs.helpers:
        self._emit_parse_int_helper()

    # Base conversion helpers (hex, bin, oct)
    if "base_conv" in self._needs.helpers:
        self._emit_base_conversion_helpers()

    # File I/O helpers
    if "file_io" in self._needs.helpers:
        self._emit_file_io_helpers()

    # Input helper
    if "input" in self._needs.helpers:
        self._emit_input_helper()

    # Arena allocator
    if "arena" in self._needs.helpers:
        self._emit_arena_helper()


def emit_split_ints_helper(self) -> None:
    """Emit split_ints() - splits string into array of integers."""
    self._output.append("/* Thread-safe strtok wrapper */")
    self._output.append("#ifdef AILANG_WINDOWS")
    self._output.append(
        "    #define AILANG_STRTOK(str, delim, saveptr) "
        "strtok_s(str, delim, saveptr)"
    )
    self._output.append("#else")
    self._output.append(
        "    #define AILANG_STRTOK(str, delim, saveptr) "
        "strtok_r(str, delim, saveptr)"
    )
    self._output.append("#endif")
    self._output.append("")
    self._output.append("/* Split string into array of integers */")
    self._output.append("typedef struct {")
    self._output.append("    int64_t *data;")
    self._output.append("    int64_t length;")
    self._output.append("    int64_t capacity;")
    self._output.append("} IntArray;")
    self._output.append("")
    self._output.append(
        "static IntArray split_ints(const char *s, const char *delim) {"
    )
    self._output.append("    IntArray result = {NULL, 0, 0};")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (!s || !delim) return result;")
    self._output.append("    ")
    self._output.append("    /* Make a copy since strtok_r modifies the string */")
    self._output.append("    char *copy = (char *)ailang_safe_malloc(strlen(s) + 1);")
    self._output.append("    if (!copy) return result;")
    self._output.append("    strcpy(copy, s);")
    self._output.append("    ")
    self._output.append("    /* Count tokens first */")
    self._output.append("    int64_t count = 0;")
    self._output.append("    char *tmp = copy;")
    self._output.append("    char *saveptr = NULL;")
    self._output.append("    char *token = AILANG_STRTOK(tmp, delim, &saveptr);")
    self._output.append("    while (token) {")
    self._output.append("        count++;")
    self._output.append("        token = AILANG_STRTOK(NULL, delim, &saveptr);")
    self._output.append("    }")
    self._output.append("    ")
    self._output.append("    /* Allocate array */")
    self._output.append(
        "    result.data = (int64_t *)ailang_request_alloc(" "count * sizeof(int64_t));"
    )
    self._output.append(
        "    if (!result.data) { ailang_safe_free(copy); return result; }"
    )
    self._output.append("    result.length = count;")
    self._output.append("    result.capacity = count;")
    self._output.append("    ")
    self._output.append("    /* Parse again */")
    self._output.append("    strcpy(copy, s);")
    self._output.append("    saveptr = NULL;")
    self._output.append("    token = AILANG_STRTOK(copy, delim, &saveptr);")
    self._output.append("    int64_t i = 0;")
    self._output.append("    while (token && i < count) {")
    self._output.append("        result.data[i++] = strtoll(token, NULL, 10);")
    self._output.append("        token = AILANG_STRTOK(NULL, delim, &saveptr);")
    self._output.append("    }")
    self._output.append("    ailang_safe_free(copy);")
    self._output.append("#else")
    self._output.append("    (void)s; (void)delim;")
    self._output.append("#endif")
    self._output.append("    return result;")
    self._output.append("}")
    self._output.append("")
    # Free helper for non-escaping IntArray locals (auto-emitted by
    # transpiler at scope exit). Just frees the data buffer.
    self._output.append(
        "AILANG_UNUSED static void ailang_int_array_free(IntArray *arr) {"
    )
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (!arr || !arr->data) return;")
    self._output.append("    ailang_safe_free(arr->data);")
    self._output.append("    arr->data = NULL;")
    self._output.append("    arr->length = 0;")
    self._output.append("    arr->capacity = 0;")
    self._output.append("#else")
    self._output.append("    (void)arr;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")


def emit_split_helper(self) -> None:
    """Emit split() - splits string into array of strings."""
    self._output.append("/* Split string into array of strings */")
    self._output.append("typedef struct {")
    self._output.append("    char **data;")
    self._output.append("    int64_t length;")
    self._output.append("    int64_t capacity;")
    self._output.append("} StringArray;")
    self._output.append("")
    self._output.append("static StringArray split(const char *s, const char *delim) {")
    self._output.append("    StringArray result = {NULL, 0, 0};")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (!s || !delim) return result;")
    self._output.append("    ")
    self._output.append("    /* Make a copy since strtok_r modifies the string. */")
    self._output.append("    /* `copy` is a transient internal buffer -- uses raw")
    self._output.append("       malloc since we free it at the end of this fn. */")
    self._output.append("    char *copy = (char *)ailang_safe_malloc(strlen(s) + 1);")
    self._output.append("    if (!copy) return result;")
    self._output.append("    strcpy(copy, s);")
    self._output.append("    ")
    self._output.append("    /* Count tokens first */")
    self._output.append("    int64_t count = 0;")
    self._output.append("    char *tmp = copy;")
    self._output.append("    char *saveptr = NULL;")
    self._output.append("    char *token = AILANG_STRTOK(tmp, delim, &saveptr);")
    self._output.append("    while (token) {")
    self._output.append("        count++;")
    self._output.append("        token = AILANG_STRTOK(NULL, delim, &saveptr);")
    self._output.append("    }")
    self._output.append("    ")
    self._output.append("    /* Result data + each token: route through arena when")
    self._output.append("       active so callers using the per-request arena")
    self._output.append("       pattern get bulk-freed by arena_reset. */")
    self._output.append(
        "    result.data = (char **)ailang_request_alloc(" "count * sizeof(char *));"
    )
    self._output.append(
        "    if (!result.data) { ailang_safe_free(copy); return result; }"
    )
    self._output.append("    result.length = count;")
    self._output.append("    result.capacity = count;")
    self._output.append("    ")
    self._output.append("    /* Parse again and copy strings */")
    self._output.append("    strcpy(copy, s);")
    self._output.append("    saveptr = NULL;")
    self._output.append("    token = AILANG_STRTOK(copy, delim, &saveptr);")
    self._output.append("    int64_t i = 0;")
    self._output.append("    while (token && i < count) {")
    self._output.append(
        "        result.data[i] = (char *)ailang_request_alloc(" "strlen(token) + 1);"
    )
    self._output.append("        if (result.data[i]) strcpy(result.data[i], token);")
    self._output.append("        i++;")
    self._output.append("        token = AILANG_STRTOK(NULL, delim, &saveptr);")
    self._output.append("    }")
    self._output.append("    ailang_safe_free(copy);")
    self._output.append("#else")
    self._output.append("    (void)s; (void)delim;")
    self._output.append("#endif")
    self._output.append("    return result;")
    self._output.append("}")
    self._output.append("")
    # Free helper for non-escaping StringArray locals (auto-emitted
    # by transpiler at scope exit). Frees each token + the data
    # array. Arena pointers no-op via ailang_safe_free's range check.
    self._output.append(
        "AILANG_UNUSED static void ailang_str_array_free(StringArray *arr) {"
    )
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (!arr || !arr->data) return;")
    self._output.append("    for (int64_t i = 0; i < arr->length; i++) {")
    self._output.append("        ailang_safe_free(arr->data[i]);")
    self._output.append("    }")
    self._output.append("    ailang_safe_free(arr->data);")
    self._output.append("    arr->data = NULL;")
    self._output.append("    arr->length = 0;")
    self._output.append("    arr->capacity = 0;")
    self._output.append("#else")
    self._output.append("    (void)arr;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")


def emit_parse_int_helper(self) -> None:
    """Emit parse_int() - parses integer from string."""
    self._output.append("/* Parse integer from string */")
    self._output.append("static int64_t parse_int(const char *s) {")
    self._output.append("#ifndef AILANG_FREESTANDING")
    self._output.append("    if (!s) return 0;")
    self._output.append("    return strtoll(s, NULL, 10);")
    self._output.append("#else")
    self._output.append("    /* Freestanding implementation */")
    self._output.append("    if (!s) return 0;")
    self._output.append("    int64_t result = 0;")
    self._output.append("    int negative = 0;")
    self._output.append("    while (*s == ' ' || *s == '\\t') s++;")
    self._output.append("    if (*s == '-') { negative = 1; s++; }")
    self._output.append("    else if (*s == '+') { s++; }")
    self._output.append("    while (*s >= '0' && *s <= '9') {")
    self._output.append("        result = result * 10 + (*s - '0');")
    self._output.append("        s++;")
    self._output.append("    }")
    self._output.append("    return negative ? -result : result;")
    self._output.append("#endif")
    self._output.append("}")
    self._output.append("")
