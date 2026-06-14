"""Hosted fd builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_common import ExprGenError

_ENOSYS = -38


def _target_is_windows(self) -> bool:
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    return "windows" in triple or sys.platform == "win32"


def _i64_const(value: int) -> ir.Constant:
    return ir.Constant(ir.IntType(64), value)


def _to_i32(self, value: ir.Value, name: str) -> ir.Value:
    i32 = ir.IntType(32)
    if isinstance(value.type, ir.IntType):
        if value.type.width == 32:
            return value
        if value.type.width < 32:
            return self.builder.zext(value, i32, name=name)
        return self.builder.trunc(value, i32, name=name)
    raise ExprGenError("fd argument must be integer")


def _to_i64(self, value: ir.Value, name: str) -> ir.Value:
    i64 = ir.IntType(64)
    if isinstance(value.type, ir.IntType):
        if value.type.width == 64:
            return value
        if value.type.width < 64:
            return self.builder.zext(value, i64, name=name)
        return self.builder.trunc(value, i64, name=name)
    if isinstance(value.type, ir.PointerType):
        return self.builder.ptrtoint(value, i64, name=name)
    raise ExprGenError("fd argument must be integer or pointer")


def _to_i8_ptr(self, value: ir.Value, name: str) -> ir.Value:
    i8_ptr = ir.IntType(8).as_pointer()
    if isinstance(value.type, ir.PointerType):
        if value.type == i8_ptr:
            return value
        return self.builder.bitcast(value, i8_ptr, name=name)
    if isinstance(value.type, ir.IntType):
        as_i64 = _to_i64(self, value, f"{name}_i64")
        return self.builder.inttoptr(as_i64, i8_ptr, name=name)
    raise ExprGenError("fd buffer argument must be integer or pointer")


def _declare(self, name: str, ret: ir.Type, args: list[ir.Type], var_arg: bool = False):
    fn = self.codegen.module.globals.get(name)
    if fn is None:
        fn = ir.Function(self.codegen.module, ir.FunctionType(ret, args, var_arg), name)
    return fn


def _native_open_flags(self, portable_flags: ir.Value) -> ir.Value:
    """Map AILang portable fd flags to target open(2)/_open flags.

    Portable bits:
      1 read, 2 write, 4 create, 8 truncate, 16 append.
    """
    i64 = ir.IntType(64)
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    is_windows = _target_is_windows(self)
    is_bsd_like = (
        "darwin" in triple
        or "apple" in triple
        or "bsd" in triple
        or sys.platform.startswith(("darwin", "freebsd", "openbsd", "netbsd"))
    )

    if is_windows:
        o_creat, o_trunc, o_append, o_binary = 0x0100, 0x0200, 0x0008, 0x8000
    elif is_bsd_like:
        o_creat, o_trunc, o_append, o_binary = 0x0200, 0x0400, 0x0008, 0
    else:
        o_creat, o_trunc, o_append, o_binary = 0x0040, 0x0200, 0x0400, 0

    def has_bit(bit: int, name: str) -> ir.Value:
        masked = self.builder.and_(
            portable_flags, ir.Constant(i64, bit), name=f"{name}_mask"
        )
        return self.builder.icmp_unsigned(
            "!=", masked, ir.Constant(i64, 0), name=f"{name}_set"
        )

    read_set = has_bit(1, "fd_read_flag")
    write_set = has_bit(2, "fd_write_flag")
    create_set = has_bit(4, "fd_create_flag")
    truncate_set = has_bit(8, "fd_truncate_flag")
    append_set = has_bit(16, "fd_append_flag")

    rdwr = self.builder.and_(read_set, write_set, name="fd_open_rdwr")
    base_write = self.builder.select(
        write_set, ir.Constant(i64, 1), ir.Constant(i64, 0), name="fd_open_base_w"
    )
    base = self.builder.select(
        rdwr, ir.Constant(i64, 2), base_write, name="fd_open_base"
    )
    native = self.builder.or_(base, ir.Constant(i64, o_binary), name="fd_open_binary")
    native = self.builder.or_(
        native,
        self.builder.select(create_set, ir.Constant(i64, o_creat), ir.Constant(i64, 0)),
        name="fd_open_create",
    )
    native = self.builder.or_(
        native,
        self.builder.select(
            truncate_set, ir.Constant(i64, o_trunc), ir.Constant(i64, 0)
        ),
        name="fd_open_truncate",
    )
    native = self.builder.or_(
        native,
        self.builder.select(
            append_set, ir.Constant(i64, o_append), ir.Constant(i64, 0)
        ),
        name="fd_open_append",
    )
    return self.builder.trunc(native, ir.IntType(32), name="fd_open_native_i32")


def _builtin_fd_open(self, args) -> ir.Value:
    """fd_open(path, flags, mode) -> int fd or -1."""
    if len(args) != 3:
        raise ExprGenError("fd_open() expects (path, flags, mode)")
    path_arg, flags_arg, mode_arg = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    path = self.generate_expr(path_arg)
    if not isinstance(path.type, ir.PointerType):
        raise ExprGenError("fd_open() path must be a string")
    if path.type != i8_ptr:
        path = self.builder.bitcast(path, i8_ptr, name="fd_open_path")
    flags = _to_i64(self, self.generate_expr(flags_arg), "fd_open_flags_i64")
    mode = _to_i32(self, self.generate_expr(mode_arg), "fd_open_mode")
    native_flags = _native_open_flags(self, flags)

    if _target_is_windows(self):
        open_fn = _declare(self, "_open", i32, [i8_ptr, i32, i32], False)
        result = self.builder.call(open_fn, [path, native_flags, mode], name="fd_open")
    else:
        open_fn = _declare(self, "open", i32, [i8_ptr, i32], True)
        result = self.builder.call(open_fn, [path, native_flags, mode], name="fd_open")
    return self.builder.sext(result, i64, name="fd_open_i64")


def _builtin_fd_read(self, args) -> ir.Value:
    """fd_read(fd, ptr, size) -> bytes read or -1."""
    if len(args) != 3:
        raise ExprGenError("fd_read() expects (fd, ptr, size)")
    fd_arg, ptr_arg, size_arg = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_read_fd")
    ptr = _to_i8_ptr(self, self.generate_expr(ptr_arg), "fd_read_ptr")
    size = _to_i64(self, self.generate_expr(size_arg), "fd_read_size")
    if _target_is_windows(self):
        read_fn = _declare(self, "_read", i32, [i32, i8_ptr, i32], False)
        size_arg = self.builder.trunc(size, i32, name="fd_read_size_i32")
        result = self.builder.call(read_fn, [fd, ptr, size_arg], name="fd_read")
        return self.builder.sext(result, i64, name="fd_read_i64")
    read_fn = _declare(self, "read", i64, [i32, i8_ptr, i64], False)
    return self.builder.call(read_fn, [fd, ptr, size], name="fd_read")


def _builtin_fd_write(self, args) -> ir.Value:
    """fd_write(fd, ptr, size) -> bytes written or -1."""
    if len(args) != 3:
        raise ExprGenError("fd_write() expects (fd, ptr, size)")
    fd_arg, ptr_arg, size_arg = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_write_fd")
    ptr = _to_i8_ptr(self, self.generate_expr(ptr_arg), "fd_write_ptr")
    size = _to_i64(self, self.generate_expr(size_arg), "fd_write_size")
    if _target_is_windows(self):
        write_fn = _declare(self, "_write", i32, [i32, i8_ptr, i32], False)
        size_arg = self.builder.trunc(size, i32, name="fd_write_size_i32")
        result = self.builder.call(write_fn, [fd, ptr, size_arg], name="fd_write")
        return self.builder.sext(result, i64, name="fd_write_i64")
    write_fn = _declare(self, "write", i64, [i32, i8_ptr, i64], False)
    return self.builder.call(write_fn, [fd, ptr, size], name="fd_write")


def _builtin_fd_close(self, args) -> ir.Value:
    """fd_close(fd) -> 0 on success or -1."""
    if len(args) != 1:
        raise ExprGenError("fd_close() expects (fd)")
    (fd_arg,) = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_close_fd")
    close_name = "_close" if _target_is_windows(self) else "close"
    close_fn = _declare(self, close_name, i32, [i32], False)
    result = self.builder.call(close_fn, [fd], name="fd_close")
    return self.builder.sext(result, i64, name="fd_close_i64")


def _builtin_fd_dup(self, args) -> ir.Value:
    """fd_dup(fd) -> duplicated fd or -1."""
    if len(args) != 1:
        raise ExprGenError("fd_dup() expects (fd)")
    (fd_arg,) = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_dup_fd")
    dup_name = "_dup" if _target_is_windows(self) else "dup"
    dup_fn = _declare(self, dup_name, i32, [i32], False)
    result = self.builder.call(dup_fn, [fd], name="fd_dup")
    return self.builder.sext(result, i64, name="fd_dup_i64")


def _builtin_fd_dup2(self, args) -> ir.Value:
    """fd_dup2(src, dst) -> 0/-1 on Windows, dst/-1 on POSIX."""
    if len(args) != 2:
        raise ExprGenError("fd_dup2() expects (src, dst)")
    src_arg, dst_arg = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    src = _to_i32(self, self.generate_expr(src_arg), "fd_dup2_src")
    dst = _to_i32(self, self.generate_expr(dst_arg), "fd_dup2_dst")
    dup2_name = "_dup2" if _target_is_windows(self) else "dup2"
    dup2_fn = _declare(self, dup2_name, i32, [i32, i32], False)
    result = self.builder.call(dup2_fn, [src, dst], name="fd_dup2")
    return self.builder.sext(result, i64, name="fd_dup2_i64")


def _builtin_fd_tell(self, args) -> ir.Value:
    """fd_tell(fd) -> current file offset or -1."""
    if len(args) != 1:
        raise ExprGenError("fd_tell() expects (fd)")
    (fd_arg,) = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_tell_fd")
    if _target_is_windows(self):
        tell_fn = _declare(self, "_lseeki64", i64, [i32, i64, i32], False)
    else:
        tell_fn = _declare(self, "lseek", i64, [i32, i64, i32], False)
    return self.builder.call(
        tell_fn,
        [fd, ir.Constant(i64, 0), ir.Constant(i32, 1)],
        name="fd_tell",
    )


def _builtin_fd_seek(self, args) -> ir.Value:
    """fd_seek(fd, offset) -> new file offset or -1."""
    if len(args) != 2:
        raise ExprGenError("fd_seek() expects (fd, offset)")
    fd_arg, offset_arg = args
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fd = _to_i32(self, self.generate_expr(fd_arg), "fd_seek_fd")
    offset = _to_i64(self, self.generate_expr(offset_arg), "fd_seek_offset")
    if _target_is_windows(self):
        seek_fn = _declare(self, "_lseeki64", i64, [i32, i64, i32], False)
    else:
        seek_fn = _declare(self, "lseek", i64, [i32, i64, i32], False)
    return self.builder.call(
        seek_fn,
        [fd, offset, ir.Constant(i32, 0)],
        name="fd_seek",
    )


def _builtin_fd_flush(self, args) -> ir.Value:
    """fd_flush() -> fflush(NULL) result."""
    if args:
        raise ExprGenError("fd_flush() expects no arguments")
    if CompilationContext.is_freestanding():
        return _i64_const(_ENOSYS)

    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fflush_fn = _declare(self, "fflush", i32, [i8_ptr], False)
    null_file = ir.Constant(i8_ptr, None)
    result = self.builder.call(fflush_fn, [null_file], name="fd_flush")
    return self.builder.sext(result, i64, name="fd_flush_i64")
