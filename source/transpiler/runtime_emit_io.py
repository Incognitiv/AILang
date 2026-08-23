"""Runtime emitter helpers for runtime emit io."""

from __future__ import annotations

__all__ = ["emit_runtime_sqlite", "emit_runtime_fileops"]


def emit_runtime_sqlite(self) -> None:
    """Emit SQLite FFI runtime helpers."""
    if "sqlite" not in self._needs.helpers:
        return

    self._output.append("/* SQLite FFI helpers */")
    self._output.append("#include <sqlite3.h>")
    self._output.append("")
    self._output.append("static int ailang_sql_last_open_status = SQLITE_OK;")
    self._output.append("")
    self._output.append(
        "static sqlite3 *ailang_sql_open_flags(const char *path, int flags) {"
    )
    self._output.append("    sqlite3 *db = NULL;")
    self._output.append("    int rc = sqlite3_open_v2(path, &db, flags, NULL);")
    self._output.append("    ailang_sql_last_open_status = rc;")
    self._output.append("    if (rc != SQLITE_OK) {")
    self._output.append(
        '        fprintf(stderr, "Cannot open database: %s\\n", sqlite3_errmsg(db));'
    )
    self._output.append("        if (db) sqlite3_close(db);")
    self._output.append("        return NULL;")
    self._output.append("    }")
    self._output.append("    return db;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static sqlite3 *sql_open(const char *path) {")
    self._output.append(
        "    int flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_URI;"
    )
    self._output.append("    return ailang_sql_open_flags(path, flags);")
    self._output.append("}")
    self._output.append("")
    self._output.append("static sqlite3 *sql_open_readonly(const char *path) {")
    self._output.append("    int flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI;")
    self._output.append("    return ailang_sql_open_flags(path, flags);")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t sql_last_open_status(void) {")
    self._output.append("    return (int64_t)ailang_sql_last_open_status;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t sql_exec(sqlite3 *db, const char *sql) {")
    self._output.append("    if (!db) return (int64_t)SQLITE_MISUSE;")
    self._output.append("    char *err_msg = NULL;")
    self._output.append("    int rc = sqlite3_exec(db, sql, NULL, NULL, &err_msg);")
    self._output.append("    if (rc != SQLITE_OK) {")
    self._output.append('        fprintf(stderr, "SQL error: %s\\n", err_msg);')
    self._output.append("        sqlite3_free(err_msg);")
    self._output.append("        return (int64_t)rc;")
    self._output.append("    }")
    self._output.append("    return (int64_t)SQLITE_OK;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static void sql_close(sqlite3 *db) {")
    self._output.append("    if (db) sqlite3_close(db);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static sqlite3_stmt *sql_prepare(sqlite3 *db, const char *sql) {"
    )
    self._output.append("    if (!db) return NULL;")
    self._output.append("    sqlite3_stmt *stmt = NULL;")
    self._output.append("    int rc = sqlite3_prepare_v2(db, sql, -1, &stmt, NULL);")
    self._output.append("    if (rc != SQLITE_OK) {")
    self._output.append(
        '        fprintf(stderr, "SQL prepare error: %s\\n", sqlite3_errmsg(db));'
    )
    self._output.append("        if (stmt) sqlite3_finalize(stmt);")
    self._output.append("        return NULL;")
    self._output.append("    }")
    self._output.append("    return stmt;")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t sql_step(sqlite3_stmt *stmt) {")
    self._output.append("    if (!stmt) return -1;")
    self._output.append("    return (int64_t)sqlite3_step(stmt);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_bind_int(sqlite3_stmt *stmt, int64_t idx, int64_t val) {"
    )
    self._output.append("    if (!stmt) return (int64_t)SQLITE_MISUSE;")
    self._output.append(
        "    return (int64_t)sqlite3_bind_int64(stmt, (int)idx, (sqlite3_int64)val);"
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_bind_text(sqlite3_stmt *stmt, int64_t idx, const char *val) {"
    )
    self._output.append("    if (!stmt) return (int64_t)SQLITE_MISUSE;")
    self._output.append(
        '    return (int64_t)sqlite3_bind_text(stmt, (int)idx, val ? val : "", -1, SQLITE_TRANSIENT);'
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int ailang_sql_append_cstr(char *dst, size_t cap, size_t *pos, const char *src) {"
    )
    self._output.append('    if (!src) src = "";')
    self._output.append("    while (*src) {")
    self._output.append("        if (*pos + 1 >= cap) return 0;")
    self._output.append("        dst[(*pos)++] = *src++;")
    self._output.append("    }")
    self._output.append("    return 1;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int ailang_sql_append_i64(char *dst, size_t cap, size_t *pos, int64_t val) {"
    )
    self._output.append("    char tmp[32];")
    self._output.append("    uint64_t n;")
    self._output.append("    int len = 0;")
    self._output.append("    if (val < 0) {")
    self._output.append("        if (*pos + 1 >= cap) return 0;")
    self._output.append("        dst[(*pos)++] = '-';")
    self._output.append("        n = (uint64_t)(-(val + 1)) + 1ULL;")
    self._output.append("    } else {")
    self._output.append("        n = (uint64_t)val;")
    self._output.append("    }")
    self._output.append("    do {")
    self._output.append("        tmp[len++] = (char)('0' + (n % 10ULL));")
    self._output.append("        n /= 10ULL;")
    self._output.append("    } while (n != 0ULL);")
    self._output.append("    while (len > 0) {")
    self._output.append("        if (*pos + 1 >= cap) return 0;")
    self._output.append("        dst[(*pos)++] = tmp[--len];")
    self._output.append("    }")
    self._output.append("    return 1;")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_bind_text_i64_parts(sqlite3_stmt *stmt, int64_t idx, const char *prefix, int64_t val, const char *suffix) {"
    )
    self._output.append("    if (!stmt) return (int64_t)SQLITE_MISUSE;")
    self._output.append("    char buf[256];")
    self._output.append("    size_t pos = 0;")
    self._output.append(
        "    if (!ailang_sql_append_cstr(buf, sizeof(buf), &pos, prefix)) return (int64_t)SQLITE_TOOBIG;"
    )
    self._output.append(
        "    if (!ailang_sql_append_i64(buf, sizeof(buf), &pos, val)) return (int64_t)SQLITE_TOOBIG;"
    )
    self._output.append(
        "    if (!ailang_sql_append_cstr(buf, sizeof(buf), &pos, suffix)) return (int64_t)SQLITE_TOOBIG;"
    )
    self._output.append("    buf[pos] = '\\0';")
    self._output.append(
        "    return (int64_t)sqlite3_bind_text(stmt, (int)idx, buf, (int)pos, SQLITE_TRANSIENT);"
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_bind_text_i64(sqlite3_stmt *stmt, int64_t idx, const char *prefix, int64_t val) {"
    )
    self._output.append(
        '    return sql_bind_text_i64_parts(stmt, idx, prefix, val, "");'
    )
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_bind_null(sqlite3_stmt *stmt, int64_t idx) {"
    )
    self._output.append("    if (!stmt) return (int64_t)SQLITE_MISUSE;")
    self._output.append("    return (int64_t)sqlite3_bind_null(stmt, (int)idx);")
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t sql_clear_bindings(sqlite3_stmt *stmt) {")
    self._output.append("    if (!stmt) return (int64_t)SQLITE_MISUSE;")
    self._output.append("    return (int64_t)sqlite3_clear_bindings(stmt);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static int64_t sql_column_int(sqlite3_stmt *stmt, int64_t col) {"
    )
    self._output.append("    if (!stmt) return 0;")
    self._output.append("    return (int64_t)sqlite3_column_int64(stmt, (int)col);")
    self._output.append("}")
    self._output.append("")
    self._output.append(
        "static const char *sql_column_text(sqlite3_stmt *stmt, int64_t col) {"
    )
    self._output.append('    if (!stmt) return "";')
    self._output.append(
        "    const unsigned char *t = sqlite3_column_text(stmt, (int)col);"
    )
    self._output.append('    return t ? (const char *)t : "";')
    self._output.append("}")
    self._output.append("")
    self._output.append("static int64_t sql_finalize(sqlite3_stmt *stmt) {")
    self._output.append("    if (!stmt) return 0;")
    self._output.append("    return (int64_t)sqlite3_finalize(stmt);")
    self._output.append("}")
    self._output.append("")


def emit_runtime_fileops(self) -> None:
    """Emit file-operation runtime helpers.

    Provides make_dir, delete_file, and move_file. file_exists is
    already emitted by _emit_runtime_file_io via fopen; we don't
    redefine it here to avoid a duplicate C function definition. POSIX
    aliases (access, mkdir, unlink, rename) resolve to these helpers
    through the dispatch lambdas above.
    """
    if "fileops" not in self._needs.helpers:
        return
    self._output.append("/* File-operation runtime helpers */")
    self._output.append("#ifdef AILANG_WINDOWS")
    self._output.append("#include <io.h>")
    self._output.append("#include <direct.h>")
    # winsock2.h MUST be included before windows.h on MinGW/MSVC --
    # otherwise winsock2.h emits a #warning. If sockets are also used
    # in this program, pre-include it here so the order holds.
    if "sockets" in self._needs.helpers:
        self._output.append("#include <winsock2.h>")
    self._output.append("#include <windows.h>")
    self._output.append("static int64_t make_dir(const char *path) {")
    self._output.append("    return (int64_t)_mkdir(path);")
    self._output.append("}")
    self._output.append("static int64_t delete_file(const char *path) {")
    self._output.append("    return (int64_t)_unlink(path);")
    self._output.append("}")
    self._output.append("static int64_t move_file(const char *src, const char *dst) {")
    self._output.append(
        "    /* MOVEFILE_REPLACE_EXISTING (0x1) makes this match POSIX rename. */"
    )
    self._output.append("    return MoveFileExA(src, dst, 1) ? 0 : -1;")
    self._output.append("}")
    self._output.append("#else")
    self._output.append("#include <sys/stat.h>")
    self._output.append("#include <unistd.h>")
    self._output.append("#include <stdio.h>")
    self._output.append("static int64_t make_dir(const char *path) {")
    self._output.append("    return (int64_t)mkdir(path, 0755);")
    self._output.append("}")
    self._output.append("static int64_t delete_file(const char *path) {")
    self._output.append("    return (int64_t)unlink(path);")
    self._output.append("}")
    self._output.append("static int64_t move_file(const char *src, const char *dst) {")
    self._output.append("    return (int64_t)rename(src, dst);")
    self._output.append("}")
    self._output.append("#endif")
    self._output.append("")


# Keep strict static checks from reporting this module as dead code when
# it is consumed via delegation wiring from runtime_emitter.
_exported_runtime_emit_helpers = (emit_runtime_sqlite, emit_runtime_fileops)
