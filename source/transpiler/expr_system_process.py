"""Process identity builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from transpiler.expr_common import ExprGenError

_ENOSYS = -38


_POSIX_ID_SYMBOLS: dict[str, str] = {
    "getppid": "getppid",
    "getuid": "getuid",
    "geteuid": "geteuid",
    "getgid": "getgid",
    "getegid": "getegid",
    # Compatibility alias for a historically requested spelling.
    "getgeid": "getegid",
}


def _target_is_windows(self) -> bool:
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    return "windows" in triple or sys.platform == "win32"


def _call_posix_i32_identity(self, builtin_name: str) -> ir.Value:
    """Call a POSIX identity function, or return -ENOSYS on Windows."""
    if _target_is_windows(self):
        return ir.Constant(ir.IntType(64), _ENOSYS)

    symbol = _POSIX_ID_SYMBOLS[builtin_name]
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fn = self.codegen.module.globals.get(symbol)
    if fn is None:
        fn_ty = ir.FunctionType(i32, [])
        fn = ir.Function(self.codegen.module, fn_ty, symbol)
    value = self.builder.call(fn, [], name=f"{builtin_name}_value")
    return self.builder.zext(value, i64, name=f"{builtin_name}_i64")


def _to_i32(self, value: ir.Value) -> ir.Value:
    i32 = ir.IntType(32)
    if not isinstance(value.type, ir.IntType):
        raise ExprGenError("process_umask(mask) mask must be integer")
    width = value.type.width
    if width == 32:
        return value
    if width > 32:
        return self.builder.trunc(value, i32)
    return self.builder.sext(value, i32)


def _builtin_getppid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("getppid() takes no arguments")
    return _call_posix_i32_identity(self, "getppid")


def _builtin_getuid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("getuid() takes no arguments")
    return _call_posix_i32_identity(self, "getuid")


def _builtin_geteuid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("geteuid() takes no arguments")
    return _call_posix_i32_identity(self, "geteuid")


def _builtin_getgid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("getgid() takes no arguments")
    return _call_posix_i32_identity(self, "getgid")


def _builtin_getegid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("getegid() takes no arguments")
    return _call_posix_i32_identity(self, "getegid")


def _builtin_getgeid(self, args) -> ir.Value:
    if args:
        raise ExprGenError("getgeid() takes no arguments")
    return _call_posix_i32_identity(self, "getgeid")


def _builtin_process_umask(self, args) -> ir.Value:
    if len(args) != 1:
        raise ExprGenError("process_umask(mask) takes one argument")
    if _target_is_windows(self):
        return ir.Constant(ir.IntType(64), _ENOSYS)

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    fn = self.codegen.module.globals.get("umask")
    if fn is None:
        fn_ty = ir.FunctionType(i32, [i32])
        fn = ir.Function(self.codegen.module, fn_ty, "umask")
    (mask_arg,) = args
    mask = _to_i32(self, self.generate_expr(mask_arg))
    value = self.builder.call(fn, [mask], name="process_umask_old")
    return self.builder.zext(value, i64, name="process_umask_old_i64")
