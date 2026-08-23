"""Control-flow and runtime-safety service helpers."""

from __future__ import annotations

import sys
from typing import Any

from llvmlite import ir


class ControlFlowEmitter:
    """Helpers for recursion/stack limits and synchronized entry locking."""

    def __init__(self, codegen: Any) -> None:
        self._cg = codegen

    def __getattr__(self, name: str) -> Any:
        # Forward unknown attributes to owning CodeGen for transparent behavior.
        return getattr(self._cg, name)

    def _get_recursion_depth_global(self) -> ir.GlobalVariable:
        """Get or create the global recursion depth counter."""
        cg = self._cg
        if cg.recursion_depth_global is None:
            cg.recursion_depth_global = ir.GlobalVariable(
                cg.module, ir.IntType(64), "__ailang_recursion_depth"
            )
            cg.recursion_depth_global.initializer = ir.Constant(ir.IntType(64), 0)
            cg.recursion_depth_global.linkage = "internal"
            # Make thread-local for safety with spawn() (M6 fix)
            cg.recursion_depth_global.thread_local = "general"
        return cg.recursion_depth_global

    def _emit_recursion_check(self, func_name: str) -> None:
        """Emit recursion depth check at function entry."""
        cg = self._cg
        depth_ptr = self._get_recursion_depth_global()
        one = ir.Constant(ir.IntType(64), 1)
        max_depth = ir.Constant(ir.IntType(64), cg.max_recursion_depth)

        # Increment depth
        current = cg.current_builder.load(depth_ptr, name="rec_depth")
        new_depth = cg.current_builder.add(current, one, name="new_depth")
        cg.current_builder.store(new_depth, depth_ptr)

        # Check if exceeded limit
        exceeded = cg.current_builder.icmp_signed(">", new_depth, max_depth)
        ok_block = cg.current_function.append_basic_block("rec_ok")
        err_block = cg.current_function.append_basic_block("rec_overflow")
        cg.current_builder.cbranch(exceeded, err_block, ok_block)

        # Error block - print message and exit
        cg.current_builder.position_at_end(err_block)
        fmt = cg.create_string_constant(
            f"Error: stack overflow in function '{func_name}' "
            f"(recursion depth > {cg.max_recursion_depth})\n"
        )
        printf = cg.get_printf()
        cg.current_builder.call(printf, [fmt])
        cg._emit_safety_trap("Stack overflow (recursion limit exceeded)")

        # Continue in ok block
        cg.current_builder.position_at_end(ok_block)

    def _emit_recursion_decrement(self) -> None:
        """Decrement recursion depth before returning."""
        cg = self._cg
        depth_ptr = self._get_recursion_depth_global()
        one = ir.Constant(ir.IntType(64), 1)
        current = cg.current_builder.load(depth_ptr, name="rec_depth_dec")
        new_depth = cg.current_builder.sub(current, one, name="dec_depth")
        cg.current_builder.store(new_depth, depth_ptr)

    def _emit_synchronized_lock(self, func_name: str) -> None:
        """Emit mutex acquisition for @synchronized functions."""
        cg = self._cg
        void_ptr = ir.IntType(8).as_pointer()
        safe_name = func_name.replace("~", "_dtor_")

        # Create global for the mutex pointer (initialized to null).
        mutex_name = f"__sync_mutex_{safe_name}"
        try:
            mutex_global = cg.module.get_global(mutex_name)
        except KeyError:
            mutex_global = ir.GlobalVariable(cg.module, ir.IntType(64), mutex_name)
            mutex_global.initializer = ir.Constant(ir.IntType(64), 0)
            mutex_global.linkage = "internal"

        # Once-flag to initialize the mutex exactly once (atomic CAS).
        flag_name = f"__sync_init_{safe_name}"
        try:
            init_flag = cg.module.get_global(flag_name)
        except KeyError:
            init_flag = ir.GlobalVariable(cg.module, ir.IntType(64), flag_name)
            init_flag.initializer = ir.Constant(ir.IntType(64), 0)
            init_flag.linkage = "internal"

        # Check if mutex needs initialization (flag == 0)
        flag_val = cg.current_builder.load(init_flag, name="sync_flag")
        needs_init = cg.current_builder.icmp_signed(
            "==", flag_val, ir.Constant(ir.IntType(64), 0)
        )
        init_block = cg.current_function.append_basic_block("sync_init")
        lock_block = cg.current_function.append_basic_block("sync_lock")
        cg.current_builder.cbranch(needs_init, init_block, lock_block)

        # Init block: allocate and initialize mutex
        cg.current_builder.position_at_end(init_block)
        old_flag = cg.current_builder.cmpxchg(
            init_flag,
            ir.Constant(ir.IntType(64), 0),
            ir.Constant(ir.IntType(64), 1),
            "seq_cst",
            "seq_cst",
        )
        did_init = cg.current_builder.extract_value(old_flag, 1, name="did_init")
        really_init = cg.current_function.append_basic_block("sync_really_init")
        cg.current_builder.cbranch(did_init, really_init, lock_block)

        cg.current_builder.position_at_end(really_init)
        raw_ptr = cg.checked_malloc(ir.Constant(ir.IntType(64), 128), "mtx_mem")
        mtx_ptr = cg.current_builder.bitcast(raw_ptr, void_ptr, name="mtx_vp")

        # Zero-initialize then call OS init
        memset_fn = cg.module.globals.get("memset")
        if memset_fn is None:
            memset_ty = ir.FunctionType(
                void_ptr, [void_ptr, ir.IntType(32), ir.IntType(64)]
            )
            memset_fn = ir.Function(cg.module, memset_ty, "memset")
        cg.current_builder.call(
            memset_fn,
            [mtx_ptr, ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(64), 128)],
        )
        init_func = cg.get_mutex_func("init")
        is_windows = "windows" in cg.module.triple.lower() or sys.platform == "win32"
        if is_windows:
            cg.current_builder.call(init_func, [mtx_ptr])
        else:
            null_ptr = ir.Constant(void_ptr, None)
            cg.current_builder.call(init_func, [mtx_ptr, null_ptr])

        handle_val = cg.current_builder.ptrtoint(
            mtx_ptr, ir.IntType(64), name="mtx_handle"
        )
        cg.current_builder.store(handle_val, mutex_global)
        cg.current_builder.branch(lock_block)

        # Lock block: load mutex handle and lock
        cg.current_builder.position_at_end(lock_block)
        mtx_handle = cg.current_builder.load(mutex_global, name="sync_mtx")
        mtx_p = cg.current_builder.inttoptr(mtx_handle, void_ptr, name="sync_mptr")
        lock_func = cg.get_mutex_func("lock")
        cg.current_builder.call(lock_func, [mtx_p])

        # Store the mutex pointer for unlock in scope cleanup
        cg._synchronized_mutex_ptr = mtx_p
