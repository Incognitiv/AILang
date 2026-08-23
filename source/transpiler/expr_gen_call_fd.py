"""C expression lowering for hosted fd builtins."""

from __future__ import annotations


def fd_c_builtin_mappings():
    return {
        "fd_open": lambda a: f"ailang_fd_open({a[0]}, {a[1]}, {a[2]})",
        "fd_read": lambda a: (
            f"ailang_fd_read({a[0]}, (int64_t)(uintptr_t)({a[1]}), {a[2]})"
        ),
        "fd_write": lambda a: (
            f"ailang_fd_write({a[0]}, (int64_t)(uintptr_t)({a[1]}), {a[2]})"
        ),
        "fd_close": lambda a: f"ailang_fd_close({a[0]})",
        "fd_dup": lambda a: f"ailang_fd_dup({a[0]})",
        "fd_dup2": lambda a: f"ailang_fd_dup2({a[0]}, {a[1]})",
        "fd_tell": lambda a: f"ailang_fd_tell({a[0]})",
        "fd_seek": lambda a: f"ailang_fd_seek({a[0]}, {a[1]})",
        "fd_flush": lambda a: "ailang_fd_flush()",
    }
