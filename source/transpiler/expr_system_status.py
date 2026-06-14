"""Hosted process-status builtins for ExprBuiltinSystemEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from runtime.modes import CompilationContext
from transpiler.expr_common import ExprGenError


def _errno_pointer_function_name(self) -> str | None:
    """Return the target libc errno accessor symbol for LLVM lowering."""
    triple = str(getattr(self.codegen.module, "triple", "")).lower()
    if "windows" in triple or sys.platform == "win32":
        return "_errno"
    if "linux" in triple or sys.platform.startswith("linux"):
        return "__errno_location"
    if (
        "darwin" in triple
        or "apple" in triple
        or "bsd" in triple
        or sys.platform.startswith(("darwin", "freebsd", "openbsd", "netbsd"))
    ):
        return "__error"
    return None


def _errno_pointer(self) -> ir.Value | None:
    """Emit or fetch the target libc errno pointer call."""
    if CompilationContext.is_freestanding():
        return None

    symbol = _errno_pointer_function_name(self)
    if symbol is None:
        return None

    i32_ptr = ir.IntType(32).as_pointer()
    fn = self.codegen.module.globals.get(symbol)
    if fn is None:
        fn_ty = ir.FunctionType(i32_ptr, [])
        fn = ir.Function(self.codegen.module, fn_ty, symbol)
    return self.builder.call(fn, [], name="errno_ptr")


def _builtin_errno_get(self, args) -> ir.Value:
    """errno_get() -> current hosted libc errno value as int."""
    if args:
        raise ExprGenError("errno_get() takes no arguments")

    i64 = ir.IntType(64)
    ptr = _errno_pointer(self)
    if ptr is None:
        return ir.Constant(i64, 0)
    value = self.builder.load(ptr, name="errno_value")
    return self.builder.sext(value, i64, name="errno_i64")


def _builtin_errno_clear(self, args) -> ir.Value:
    """errno_clear() -> clear hosted libc errno and return 0."""
    if args:
        raise ExprGenError("errno_clear() takes no arguments")

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    ptr = _errno_pointer(self)
    if ptr is not None:
        self.builder.store(ir.Constant(i32, 0), ptr)
    return ir.Constant(i64, 0)


def _builtin_errno_set(self, args) -> ir.Value:
    """errno_set(value) -> set hosted libc errno and return value."""
    if len(args) != 1:
        raise ExprGenError("errno_set() takes exactly one argument")
    (value_arg,) = args

    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    value = self.generate_expr(value_arg)
    ptr = _errno_pointer(self)
    if ptr is not None:
        if isinstance(value.type, ir.IntType) and value.type.width < 32:
            stored = self.builder.sext(value, i32)
        elif isinstance(value.type, ir.IntType) and value.type.width > 32:
            stored = self.builder.trunc(value, i32)
        elif value.type == i32:
            stored = value
        else:
            stored = self.builder.trunc(value, i32)
        self.builder.store(stored, ptr)
    if isinstance(value.type, ir.IntType) and value.type.width < 64:
        return self.builder.sext(value, i64)
    if isinstance(value.type, ir.IntType) and value.type.width > 64:
        return self.builder.trunc(value, i64)
    return value
