"""
ProfilingEmitter - service for LLVM profile instrumentation helpers.

Phase A3 of the LLVM-side architectural pivot. Moves profiling-related
helper methods off ``CodeGen`` while preserving legacy call sites via
``CodeGen.__getattr__``:

    _get_prof_enter_func
    _get_prof_exit_func
    _get_prof_name_const
    _is_profile_skipped
    emit_profile_enter
    emit_profile_exit
"""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class ProfilingEmitter:
    """Profile instrumentation emitter for the LLVM backend."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Cache fields live on CodeGen during migration.
        return getattr(self._cg, name)

    def _get_prof_enter_func(self) -> ir.Function:
        """Lazy declaration of __ailang_prof_enter(const char *name)."""
        if self._cg._prof_enter_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            ty = ir.FunctionType(ir.VoidType(), [char_ptr])
            self._cg._prof_enter_func = ir.Function(
                self._cg.module, ty, "__ailang_prof_enter"
            )
        return self._cg._prof_enter_func

    def _get_prof_exit_func(self) -> ir.Function:
        """Lazy declaration of __ailang_prof_exit(const char *name)."""
        if self._cg._prof_exit_func is None:
            char_ptr = ir.IntType(8).as_pointer()
            ty = ir.FunctionType(ir.VoidType(), [char_ptr])
            self._cg._prof_exit_func = ir.Function(
                self._cg.module, ty, "__ailang_prof_exit"
            )
        return self._cg._prof_exit_func

    def _get_prof_name_const(self, func_name: str) -> ir.Value:
        """Return an i8* pointing at a NUL-terminated copy of func_name."""
        if func_name in self._cg._prof_name_consts:
            return self._cg._prof_name_consts[func_name]
        encoded = (func_name + "\0").encode("utf-8")
        arr_ty = ir.ArrayType(ir.IntType(8), len(encoded))
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in func_name)
        global_name = f"__prof_name_{safe}"
        base = global_name
        suffix = 0
        existing_names = {g.name for g in self._cg.module.global_values}
        while global_name in existing_names:
            suffix += 1
            global_name = f"{base}_{suffix}"
        gvar = ir.GlobalVariable(self._cg.module, arr_ty, name=global_name)
        gvar.initializer = ir.Constant(arr_ty, bytearray(encoded))
        gvar.linkage = "internal"
        gvar.global_constant = True
        ptr = gvar.bitcast(ir.IntType(8).as_pointer())
        self._cg._prof_name_consts[func_name] = ptr
        return ptr

    def _is_profile_skipped(self, func_name: str) -> bool:
        """Skip profiler internals to avoid recursion and noisy output."""
        if func_name.startswith("__ailang_prof_"):
            return True
        return bool(func_name.startswith("__prof_"))

    def emit_profile_enter(self, func_name: str) -> None:
        """Inject __ailang_prof_enter at the current builder position."""
        if not self._cg.profile_enabled:
            return
        if self._is_profile_skipped(func_name):
            return
        if self._cg.current_builder.block.is_terminated:
            return
        name_ptr = self._get_prof_name_const(func_name)
        self._cg.current_builder.call(self._get_prof_enter_func(), [name_ptr])

    def emit_profile_exit(self, func_name: str) -> None:
        """Inject __ailang_prof_exit at the current builder position."""
        if not self._cg.profile_enabled:
            return
        if self._is_profile_skipped(func_name):
            return
        if self._cg.current_builder.block.is_terminated:
            return
        name_ptr = self._get_prof_name_const(func_name)
        self._cg.current_builder.call(self._get_prof_exit_func(), [name_ptr])
