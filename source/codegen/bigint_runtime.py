"""
BigIntRuntime - service for LLVM bigint type/runtime declarations.

Phase A4 of the LLVM-side architectural pivot. Lifts the bigint helper
methods off ``CodeGen`` while preserving legacy call sites via
``CodeGen.__getattr__``.
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class BigIntRuntime:
    """Bigint type/runtime declaration helpers for the LLVM backend."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cg, name)

    def _get_bigint_type(self) -> ir.PointerType:
        """Get the bigint pointer type."""
        if self._cg._bigint_type is None:
            self._cg._bigint_type = ir.LiteralStructType(
                [
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64),
                    ir.IntType(64).as_pointer(),
                ]
            )
        return self._cg._bigint_type.as_pointer()

    def _get_bigint_new(self) -> ir.Function:
        if self._cg._bigint_new_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [ir.IntType(64)])
            self._cg._bigint_new_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_new"
            )
        return self._cg._bigint_new_func

    def _get_bigint_from_int(self) -> ir.Function:
        if self._cg._bigint_from_int_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [ir.IntType(64)])
            self._cg._bigint_from_int_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_from_int"
            )
        return self._cg._bigint_from_int_func

    def _get_bigint_add(self) -> ir.Function:
        if self._cg._bigint_add_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr])
            self._cg._bigint_add_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_add"
            )
        return self._cg._bigint_add_func

    def _get_bigint_sub(self) -> ir.Function:
        if self._cg._bigint_sub_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr])
            self._cg._bigint_sub_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_sub"
            )
        return self._cg._bigint_sub_func

    def _get_bigint_mul(self) -> ir.Function:
        if self._cg._bigint_mul_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr])
            self._cg._bigint_mul_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_mul"
            )
        return self._cg._bigint_mul_func

    def _get_bigint_div(self) -> ir.Function:
        if self._cg._bigint_div_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, bigint_ptr])
            self._cg._bigint_div_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_div"
            )
        return self._cg._bigint_div_func

    def _get_bigint_pow(self) -> ir.Function:
        if self._cg._bigint_pow_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(bigint_ptr, [bigint_ptr, ir.IntType(64)])
            self._cg._bigint_pow_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_pow"
            )
        return self._cg._bigint_pow_func

    def _get_bigint_cmp(self) -> ir.Function:
        if self._cg._bigint_cmp_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(ir.IntType(32), [bigint_ptr, bigint_ptr])
            self._cg._bigint_cmp_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_cmp"
            )
        return self._cg._bigint_cmp_func

    def _get_bigint_print(self) -> ir.Function:
        if self._cg._bigint_print_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(ir.VoidType(), [bigint_ptr])
            self._cg._bigint_print_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_print"
            )
        return self._cg._bigint_print_func

    def _get_bigint_digits(self) -> ir.Function:
        if self._cg._bigint_digits_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(ir.IntType(64), [bigint_ptr])
            self._cg._bigint_digits_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_digits"
            )
        return self._cg._bigint_digits_func

    def _get_bigint_free(self) -> ir.Function:
        if self._cg._bigint_free_func is None:
            bigint_ptr = self._get_bigint_type()
            fn_ty = ir.FunctionType(ir.VoidType(), [bigint_ptr])
            self._cg._bigint_free_func = ir.Function(
                self._cg.module, fn_ty, "ailang_bigint_free"
            )
        return self._cg._bigint_free_func

    def is_bigint_type(self, llvm_type: ir.Type) -> bool:
        if self._cg._bigint_type is None:
            return False
        return llvm_type == self._cg._bigint_type.as_pointer()
