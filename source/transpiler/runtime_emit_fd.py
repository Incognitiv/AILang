"""C runtime emitter for hosted fd builtins."""

from __future__ import annotations

from typing import Any


def emit_runtime_fd(emitter: Any) -> None:
    """Emit hosted file-descriptor helpers.

    `fd_open` takes AILang portable flags:
      1 read, 2 write, 4 create, 8 truncate, 16 append.
    The helper maps them to the target CRT/libc flags.
    """
    o = emitter._output
    o.append("/* Hosted fd runtime helpers */")
    o.append("#ifndef AILANG_FREESTANDING")
    o.append("    #include <errno.h>")
    o.append("    #include <stdio.h>")
    o.append("    #include <sys/stat.h>")
    o.append("    #if defined(AILANG_WINDOWS)")
    o.append("        #include <io.h>")
    o.append("        #include <fcntl.h>")
    o.append("    #else")
    o.append("        #include <fcntl.h>")
    o.append("        #include <unistd.h>")
    o.append("    #endif")
    o.append("#endif")
    o.append("")
    o.append("AILANG_UNUSED static int ailang_fd_native_open_flags(int64_t flags) {")
    o.append("#ifdef AILANG_FREESTANDING")
    o.append("    (void)flags;")
    o.append("    return 0;")
    o.append("#else")
    o.append("    int native = 0;")
    o.append("    int want_read = (flags & 1) != 0;")
    o.append("    int want_write = (flags & 2) != 0;")
    o.append("    if (want_read && want_write) native |= O_RDWR;")
    o.append("    else if (want_write) native |= O_WRONLY;")
    o.append("    else native |= O_RDONLY;")
    o.append("    if ((flags & 4) != 0) native |= O_CREAT;")
    o.append("    if ((flags & 8) != 0) native |= O_TRUNC;")
    o.append("    if ((flags & 16) != 0) native |= O_APPEND;")
    o.append("#if defined(AILANG_WINDOWS) && defined(_O_BINARY)")
    o.append("    native |= _O_BINARY;")
    o.append("#endif")
    o.append("    return native;")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_fd_dup(int64_t fd) {")
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)_dup((int)fd);")
    o.append("#else")
    o.append("    return (int64_t)dup((int)fd);")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_fd_dup2(int64_t src, int64_t dst) {")
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)src; (void)dst;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)_dup2((int)src, (int)dst);")
    o.append("#else")
    o.append("    return (int64_t)dup2((int)src, (int)dst);")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_fd_tell(int64_t fd) {")
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)_lseeki64((int)fd, 0, SEEK_CUR);")
    o.append("#else")
    o.append("    return (int64_t)lseek((int)fd, 0, SEEK_CUR);")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append(
        "AILANG_UNUSED static int64_t ailang_fd_seek(int64_t fd, int64_t offset) {"
    )
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd; (void)offset;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)_lseeki64((int)fd, offset, SEEK_SET);")
    o.append("#else")
    o.append("    return (int64_t)lseek((int)fd, offset, SEEK_SET);")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_fd_flush(void) {")
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    return -38;")
    o.append("#else")
    o.append("    return (int64_t)fflush(NULL);")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append(
        "AILANG_UNUSED static int64_t ailang_fd_open("
        "const char *path, int64_t flags, int64_t mode) {"
    )
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)path; (void)flags; (void)mode;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append(
        "    return (int64_t)_open(path, ailang_fd_native_open_flags(flags), (int)mode);"
    )
    o.append("#else")
    o.append(
        "    return (int64_t)open(path, ailang_fd_native_open_flags(flags), (mode_t)mode);"
    )
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append(
        "AILANG_UNUSED static int64_t ailang_fd_read("
        "int64_t fd, int64_t ptr, int64_t size) {"
    )
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd; (void)ptr; (void)size;")
    o.append("    return -38;")
    o.append("#else")
    o.append("    if (ptr == 0 || size < 0) { errno = EINVAL; return -1; }")
    o.append("#if defined(AILANG_WINDOWS)")
    o.append("    if (size > 2147483647LL) { errno = EINVAL; return -1; }")
    o.append(
        "    return (int64_t)_read((int)fd, (void *)(uintptr_t)ptr, (unsigned int)size);"
    )
    o.append("#else")
    o.append("    return (int64_t)read((int)fd, (void *)(uintptr_t)ptr, (size_t)size);")
    o.append("#endif")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append(
        "AILANG_UNUSED static int64_t ailang_fd_write("
        "int64_t fd, int64_t ptr, int64_t size) {"
    )
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd; (void)ptr; (void)size;")
    o.append("    return -38;")
    o.append("#else")
    o.append("    if (ptr == 0 || size < 0) { errno = EINVAL; return -1; }")
    o.append("#if defined(AILANG_WINDOWS)")
    o.append("    if (size > 2147483647LL) { errno = EINVAL; return -1; }")
    o.append(
        "    return (int64_t)_write((int)fd, (const void *)(uintptr_t)ptr, (unsigned int)size);"
    )
    o.append("#else")
    o.append(
        "    return (int64_t)write((int)fd, (const void *)(uintptr_t)ptr, (size_t)size);"
    )
    o.append("#endif")
    o.append("#endif")
    o.append("}")
    o.append("")
    o.append("AILANG_UNUSED static int64_t ailang_fd_close(int64_t fd) {")
    o.append("#if defined(AILANG_FREESTANDING)")
    o.append("    (void)fd;")
    o.append("    return -38;")
    o.append("#elif defined(AILANG_WINDOWS)")
    o.append("    return (int64_t)_close((int)fd);")
    o.append("#else")
    o.append("    return (int64_t)close((int)fd);")
    o.append("#endif")
    o.append("}")
    o.append("")
