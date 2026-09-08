"""Split and parse-int runtime helper emitters."""

from __future__ import annotations


def emit_split_ints_helper(self) -> None:
    """Emit split_ints() - splits string into array of integers."""
    self._output.append("/* Thread-safe strtok wrapper */")
    self._output.append("#ifndef AILANG_STRTOK")
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
    self._output.append("/* Thread-safe strtok wrapper */")
    self._output.append("#ifndef AILANG_STRTOK")
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
    self._output.append("#endif")
    self._output.append("")
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
