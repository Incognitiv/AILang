"""Stack pointer helpers for ExprBuiltinMemoryEmitter."""

from __future__ import annotations

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ExprGenError


def _builtin_ptr_array(self, args: list) -> ir.Value:
    """Build a null-terminated pointer array in the current stack frame."""
    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    array_ty = ir.ArrayType(i64, len(args) + 1)
    arr = self.builder.alloca(array_ty, name="ptr_array")
    for index, arg in enumerate(args):
        value = self.generate_expr(arg)
        if isinstance(value.type, ir.PointerType):
            value = self.builder.ptrtoint(value, i64, name=f"ptr_array_{index}_i")
        elif isinstance(value.type, ir.IntType) and value.type.width != 64:
            value = self.builder.zext(value, i64, name=f"ptr_array_{index}_z")
        slot = self.builder.gep(
            arr,
            [ir.Constant(i32, 0), ir.Constant(i32, index)],
            name=f"ptr_array_{index}_slot",
        )
        self.builder.store(value, slot)
    sentinel = self.builder.gep(
        arr,
        [ir.Constant(i32, 0), ir.Constant(i32, len(args))],
        name="ptr_array_null_slot",
    )
    self.builder.store(ir.Constant(i64, 0), sentinel)
    first = self.builder.gep(
        arr,
        [ir.Constant(i32, 0), ir.Constant(i32, 0)],
        name="ptr_array_first",
    )
    return self.builder.ptrtoint(first, i64, name="ptr_array_i")


def _builtin_stack_alloc(self, args: list) -> ir.Value:
    """Allocate raw bytes in the current stack frame."""
    if len(args) != 1:
        raise ExprGenError("stack_alloc() expects 1 argument")
    size = self.generate_expr(args[ARG_FIRST])
    if size.type != ir.IntType(64):
        size = self.builder.zext(size, ir.IntType(64), name="stack_alloc_size")
    ptr = self.builder.alloca(ir.IntType(8), size=size, name="stack_alloc_ptr")
    return self.builder.ptrtoint(ptr, ir.IntType(64), name="stack_alloc_i")
