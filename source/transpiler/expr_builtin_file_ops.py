"""Filesystem operation builtins for ExprBuiltinFileEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ARG_THIRD, ExprGenError


def _builtin_access(self, args):
    """access(path[, mode]) -> 1 if host access check succeeds.

    Mode follows POSIX bit values where possible: F_OK=0, X_OK=1,
    W_OK=2, R_OK=4. On Windows, execute mode is approximated by
    executable filename suffix after existence/non-directory checks.
    """
    if len(args) not in (1, 2):
        raise ExprGenError("access() expects (path[, mode])")
    char_ptr = ir.IntType(8).as_pointer()
    path = self.generate_expr(args[ARG_FIRST])
    mode = (
        ir.Constant(ir.IntType(32), 0)
        if len(args) == 1
        else self.builder.trunc(
            self.generate_expr(args[ARG_SECOND]),
            ir.IntType(32),
            name="access_mode_i32",
        )
    )

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    access_name = "_access" if is_windows else "access"
    access_ty = ir.FunctionType(ir.IntType(32), [char_ptr, ir.IntType(32)])
    access_fn = self.codegen._declare_external(access_name, access_ty)
    if is_windows:
        # The MSVCRT mode bits cover existence/read/write, not execute.
        # Treat X_OK as existence for LLVM-hosted Windows; the C backend
        # uses a stricter suffix-aware helper for command-search work.
        x_only = self.builder.icmp_signed(
            "==", mode, ir.Constant(ir.IntType(32), 1), name="access_x_only"
        )
        mode = self.builder.select(
            x_only, ir.Constant(ir.IntType(32), 0), mode, name="access_win_mode"
        )
    rc = self.builder.call(access_fn, [path, mode], name="access_rc")
    is_zero = self.builder.icmp_signed(
        "==", rc, ir.Constant(ir.IntType(32), 0), name="access_is_ok"
    )
    one64 = ir.Constant(ir.IntType(64), 1)
    zero64 = ir.Constant(ir.IntType(64), 0)
    return self.builder.select(is_zero, one64, zero64, name="access_result")


def _builtin_delete_file(self, args):
    """delete_file(path) -> 0 on success, non-zero on failure.

    Wraps unlink(path) on POSIX, _unlink(path) on Windows. Both have
    identical signature (int return, errno on failure) so the platform
    difference is just the symbol name.
    """
    if len(args) != 1:
        raise ExprGenError("delete_file() expects (path)")
    char_ptr = ir.IntType(8).as_pointer()
    path = self.generate_expr(args[ARG_FIRST])

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    unlink_name = "_unlink" if is_windows else "unlink"
    unlink_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
    unlink_fn = self.codegen._declare_external(unlink_name, unlink_ty)
    rc = self.builder.call(unlink_fn, [path], name="unlink_rc")
    return self.builder.sext(rc, ir.IntType(64), name="unlink_rc_64")


def _builtin_file_can_execute(self, args):
    """file_can_execute(path) -> 1 if host says path can execute/search."""
    if len(args) != 1:
        raise ExprGenError("file_can_execute() expects (path)")
    char_ptr = ir.IntType(8).as_pointer()
    path = self.generate_expr(args[ARG_FIRST])

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    if is_windows:
        access_ty = ir.FunctionType(ir.IntType(32), [char_ptr, ir.IntType(32)])
        access_fn = self.codegen._declare_external("_access", access_ty)
        rc = self.builder.call(
            access_fn,
            [path, ir.Constant(ir.IntType(32), 0)],
            name="access_x_rc",
        )
        is_zero = self.builder.icmp_signed(
            "==", rc, ir.Constant(ir.IntType(32), 0), name="access_x_is_ok"
        )
        one64 = ir.Constant(ir.IntType(64), 1)
        zero64 = ir.Constant(ir.IntType(64), 0)
        return self.builder.select(
            is_zero, one64, zero64, name="file_can_execute_result"
        )
    access_ty = ir.FunctionType(ir.IntType(32), [char_ptr, ir.IntType(32)])
    access_fn = self.codegen._declare_external("access", access_ty)
    x_ok = ir.Constant(ir.IntType(32), 1)
    rc = self.builder.call(access_fn, [path, x_ok], name="access_x_rc")
    is_zero = self.builder.icmp_signed(
        "==", rc, ir.Constant(ir.IntType(32), 0), name="access_x_is_ok"
    )
    one64 = ir.Constant(ir.IntType(64), 1)
    zero64 = ir.Constant(ir.IntType(64), 0)
    return self.builder.select(is_zero, one64, zero64, name="file_can_execute_result")


def _builtin_file_exists(self, args):
    """file_exists(path) -> 1 if file or directory exists, 0 otherwise."""
    if len(args) != 1:
        raise ExprGenError("file_exists() expects (path)")
    return self._builtin_access(args)


def _builtin_file_size(self, args):
    """file_size(filename) - Get file size in bytes, returns -1 on error."""
    CompilationContext.require_feature("file_io", "file_size()")

    if len(args) != 1:
        raise ExprGenError("file_size() expects filename")

    filename = self.generate_expr(args[ARG_FIRST])

    # Open file in binary read mode
    file_ptr = self.builder.call(
        self.codegen.get_fopen(),
        [filename, self.codegen.create_string_constant("rb")],
        name="fopen",
    )

    null_ptr = ir.Constant(file_ptr.type, None)
    file_ok = self.builder.icmp_unsigned("!=", file_ptr, null_ptr, name="file_ok")
    success_block = self.function.append_basic_block("fsize_success")
    error_block = self.function.append_basic_block("fsize_error")
    merge_block = self.function.append_basic_block("fsize_merge")
    self.builder.cbranch(file_ok, success_block, error_block)

    # Error block - return -1
    self.builder.position_at_end(error_block)
    error_result = ir.Constant(ir.IntType(64), -1)
    self.builder.branch(merge_block)
    error_end = self.builder.block

    # Success block - seek to end and get position. 64-bit offset
    # on Windows (LLP64) - get_fseek/get_ftell are now backed by
    self.builder.position_at_end(success_block)
    zero64 = ir.Constant(ir.IntType(64), 0)
    seek_end = ir.Constant(ir.IntType(32), 2)  # SEEK_END
    self.builder.call(self.codegen.get_fseek(), [file_ptr, zero64, seek_end])
    file_size = self.builder.call(
        self.codegen.get_ftell(), [file_ptr], name="file_size"
    )
    self.builder.call(self.codegen.get_fclose(), [file_ptr])
    self.builder.branch(merge_block)
    success_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(ir.IntType(64), name="fsize_result")
    phi.add_incoming(error_result, error_end)
    phi.add_incoming(file_size, success_end)
    return phi


def _builtin_make_dir(self, args):
    """make_dir(path) -> 0 on success, non-zero on failure.

    Single-level mkdir; caller ensures parent directory exists.
    Wraps mkdir(path, 0755) on POSIX, _mkdir(path) on Windows.
    """
    if len(args) != 1:
        raise ExprGenError("make_dir() expects (path)")
    char_ptr = ir.IntType(8).as_pointer()
    path = self.generate_expr(args[ARG_FIRST])

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    if is_windows:
        mkdir_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
        mkdir_fn = self.codegen._declare_external("_mkdir", mkdir_ty)
        rc = self.builder.call(mkdir_fn, [path], name="mkdir_rc")
    else:
        mkdir_ty = ir.FunctionType(ir.IntType(32), [char_ptr, ir.IntType(32)])
        mkdir_fn = self.codegen._declare_external("mkdir", mkdir_ty)
        mode = ir.Constant(ir.IntType(32), 0o755)
        rc = self.builder.call(mkdir_fn, [path, mode], name="mkdir_rc")
    return self.builder.sext(rc, ir.IntType(64), name="mkdir_rc_64")


def _builtin_move_file(self, args):
    """move_file(old, new) -> 0 on success, non-zero on failure.

    On POSIX, calls rename(old, new) which atomically replaces an
    existing destination. On Windows, the C runtime's rename FAILS if
    the destination exists, so we route through MoveFileExA with the
    MOVEFILE_REPLACE_EXISTING flag (0x1) to get matching semantics.
    """
    if len(args) != 2:
        raise ExprGenError("move_file() expects (old, new)")
    char_ptr = ir.IntType(8).as_pointer()
    old_path = self.generate_expr(args[ARG_FIRST])
    new_path = self.generate_expr(args[ARG_SECOND])

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    if is_windows:
        # BOOL MoveFileExA(LPCSTR existing, LPCSTR new, DWORD flags)
        move_ty = ir.FunctionType(ir.IntType(32), [char_ptr, char_ptr, ir.IntType(32)])
        move_fn = self.codegen._declare_external("MoveFileExA", move_ty)
        replace_existing = ir.Constant(ir.IntType(32), 1)
        bool_rc = self.builder.call(
            move_fn, [old_path, new_path, replace_existing], name="movefile_rc"
        )
        # Non-zero BOOL = success - return 0; zero BOOL = failure - return -1
        is_zero = self.builder.icmp_signed(
            "==", bool_rc, ir.Constant(ir.IntType(32), 0), name="move_failed"
        )
        zero64 = ir.Constant(ir.IntType(64), 0)
        neg_one64 = ir.Constant(ir.IntType(64), -1)
        return self.builder.select(is_zero, neg_one64, zero64, name="move_rc_64")
    rename_ty = ir.FunctionType(ir.IntType(32), [char_ptr, char_ptr])
    rename_fn = self.codegen._declare_external("rename", rename_ty)
    rc = self.builder.call(rename_fn, [old_path, new_path], name="rename_rc")
    return self.builder.sext(rc, ir.IntType(64), name="rename_rc_64")


def _builtin_read_bytes(self, args):
    """read_bytes(filename, size_ptr) - Read binary file, return data pointer.

    Stores actual bytes read at size_ptr. Returns 0 on error.
    Caller must free() the returned buffer.
    """
    CompilationContext.require_feature("file_io", "read_bytes()")

    if len(args) != 2:
        raise ExprGenError("read_bytes() expects (filename, size_ptr)")

    filename = self.generate_expr(args[ARG_FIRST])
    size_ptr = self.generate_expr(args[ARG_SECOND])

    file_ptr, success_block, error_block, merge_block = self._open_binary_read(
        filename, "rbytes"
    )

    # Error block - store 0 and return null
    self.builder.position_at_end(error_block)
    zero64 = ir.Constant(ir.IntType(64), 0)
    size_ptr_cast = self.builder.inttoptr(
        size_ptr, ir.IntType(64).as_pointer(), name="size_ptr_i64"
    )
    self.builder.store(zero64, size_ptr_cast)
    error_result = ir.Constant(ir.IntType(64), 0)
    self.builder.branch(merge_block)
    error_end = self.builder.block

    # Success block - get size, allocate, read. 64-bit-safe seek.
    self.builder.position_at_end(success_block)
    file_size = self._read_file_size(file_ptr)
    self._guard_read_allocation_size(
        file_size, "read_bytes", "rb_size_ok", "rb_size_too_large"
    )
    # Allocate buffer
    buffer = self.builder.call(
        self.codegen.get_malloc(), [file_size], name="read_buffer"
    )

    # Read all bytes
    one64 = ir.Constant(ir.IntType(64), 1)
    bytes_read = self.builder.call(
        self.codegen.get_fread(),
        [buffer, one64, file_size, file_ptr],
        name="bytes_read",
    )
    self.builder.call(self.codegen.get_fclose(), [file_ptr])

    # Store actual bytes read
    size_ptr_cast2 = self.builder.inttoptr(
        size_ptr, ir.IntType(64).as_pointer(), name="size_ptr_i64_2"
    )
    self.builder.store(bytes_read, size_ptr_cast2)

    # Return buffer as int (pointer)
    buf_int = self.builder.ptrtoint(buffer, ir.IntType(64), name="buf_ptr")
    self.builder.branch(merge_block)
    success_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(ir.IntType(64), name="rbytes_result")
    phi.add_incoming(error_result, error_end)
    phi.add_incoming(buf_int, success_end)
    return phi


def _builtin_write_bytes(self, args):
    """write_bytes(filename, ptr, size) - Write raw bytes to file.

    Returns 1 on success, 0 on error.
    """
    CompilationContext.require_feature("file_io", "write_bytes()")

    if len(args) != 3:
        raise ExprGenError("write_bytes() expects (filename, ptr, size)")

    filename = self.generate_expr(args[ARG_FIRST])
    ptr = self.generate_expr(args[ARG_SECOND])
    size = self.generate_expr(args[ARG_THIRD])

    # Convert int pointer to actual pointer
    ptr_cast = self.builder.inttoptr(ptr, ir.IntType(8).as_pointer(), name="data_ptr")

    # Open file in binary write mode
    file_ptr = self.builder.call(
        self.codegen.get_fopen(),
        [filename, self.codegen.create_string_constant("wb")],
        name="fopen",
    )

    null_ptr = ir.Constant(file_ptr.type, None)
    file_ok = self.builder.icmp_unsigned("!=", file_ptr, null_ptr, name="file_ok")
    success_block = self.function.append_basic_block("wbytes_success")
    error_block = self.function.append_basic_block("wbytes_error")
    merge_block = self.function.append_basic_block("wbytes_merge")
    self.builder.cbranch(file_ok, success_block, error_block)

    # Error block
    self.builder.position_at_end(error_block)
    error_result = ir.Constant(ir.IntType(64), 0)
    self.builder.branch(merge_block)
    error_end = self.builder.block

    # Success block
    self.builder.position_at_end(success_block)
    one64 = ir.Constant(ir.IntType(64), 1)
    self.builder.call(self.codegen.get_fwrite(), [ptr_cast, one64, size, file_ptr])
    self.builder.call(self.codegen.get_fclose(), [file_ptr])
    success_result = ir.Constant(ir.IntType(64), 1)
    self.builder.branch(merge_block)
    success_end = self.builder.block

    self.builder.position_at_end(merge_block)
    phi = self.builder.phi(ir.IntType(64), name="wbytes_result")
    phi.add_incoming(error_result, error_end)
    phi.add_incoming(success_result, success_end)
    return phi
