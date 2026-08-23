"""Synchronization and system builtins for ExprBuiltinThreadingEmitter."""

from __future__ import annotations

import sys

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError


def _sync_alloc_and_init(self, size: int, init_op: str, getter) -> ir.Value:
    """Allocate and initialize a synchronization primitive."""
    void_ptr = ir.IntType(8).as_pointer()
    sz = ir.Constant(ir.IntType(64), size)
    raw_ptr = self.codegen.checked_malloc(sz, "sync_ptr")
    ptr = self.builder.bitcast(raw_ptr, void_ptr, name="sync_vptr")

    # Zero-initialize via calloc-style: write zeroes using a byte-loop-free path
    # Use LLVM memset intrinsic
    memset_fn = self.codegen.module.globals.get("memset")
    if memset_fn is None:
        memset_ty = ir.FunctionType(
            void_ptr, [void_ptr, ir.IntType(32), ir.IntType(64)]
        )
        memset_fn = ir.Function(self.codegen.module, memset_ty, "memset")
    self.builder.call(memset_fn, [ptr, ir.Constant(ir.IntType(32), 0), sz])

    init_func = getter(init_op)
    if init_func is None:
        raise ExprGenError(
            f"Sync init operation '{init_op}' not available on this platform"
        )
    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    if is_windows:
        self.builder.call(init_func, [ptr])
    else:
        null_ptr = ir.Constant(void_ptr, None)
        self.builder.call(init_func, [ptr, null_ptr])

    return self.builder.ptrtoint(ptr, ir.IntType(64), name="sync_handle")


def _sync_call_void(self, args, op: str, getter, name: str, nargs: int = 1) -> ir.Value:
    """Call a synchronization function that takes a handle."""
    if len(args) != nargs:
        raise ExprGenError(f"{name}() requires {nargs} argument(s)")
    void_ptr = ir.IntType(8).as_pointer()
    handle = self.generate_expr(args[ARG_FIRST])
    ptr = self.builder.inttoptr(handle, void_ptr, name=f"{name}_ptr")
    func = getter(op)
    if func is None:
        return ir.Constant(ir.IntType(64), 0)  # No-op on this platform
    self.builder.call(func, [ptr])
    return ir.Constant(ir.IntType(64), 0)


def _sync_get_free(self) -> ir.Function:
    """Get or declare the C free() function."""
    free_fn = self.codegen.module.globals.get("free")
    if free_fn is None:
        void_ptr = ir.IntType(8).as_pointer()
        free_ty = ir.FunctionType(ir.VoidType(), [void_ptr])
        free_fn = ir.Function(self.codegen.module, free_ty, "free")
    return free_fn


def _builtin_mutex_create(self, args) -> ir.Value:
    """Create a mutex. Returns handle (int).

    Ada equivalent: protected type (implicit mutex on entry)
    """
    if args:
        raise ExprGenError("mutex_create() takes no arguments")
    return self._sync_alloc_and_init(128, "init", self.codegen.get_mutex_func)


def _builtin_mutex_lock(self, args) -> ir.Value:
    """Acquire a mutex lock (blocking)."""
    return self._sync_call_void(args, "lock", self.codegen.get_mutex_func, "mutex_lock")


def _builtin_mutex_unlock(self, args) -> ir.Value:
    """Release a mutex lock."""
    return self._sync_call_void(
        args, "unlock", self.codegen.get_mutex_func, "mutex_unlock"
    )


def _builtin_mutex_destroy(self, args) -> ir.Value:
    """Destroy a mutex and free memory."""
    if len(args) != 1:
        raise ExprGenError("mutex_destroy() requires 1 argument")
    void_ptr = ir.IntType(8).as_pointer()
    handle = self.generate_expr(args[ARG_FIRST])
    ptr = self.builder.inttoptr(handle, void_ptr, name="mtx_dptr")
    destroy_func = self.codegen.get_mutex_func("destroy")
    self.builder.call(destroy_func, [ptr])
    self.builder.call(self._sync_get_free(), [ptr])
    return ir.Constant(ir.IntType(64), 0)


def _builtin_cond_create(self, args) -> ir.Value:
    """Create a condition variable. Returns handle (int).

    Ada equivalent: entry barrier (when Guard => ...)
    """
    if args:
        raise ExprGenError("cond_create() takes no arguments")
    return self._sync_alloc_and_init(128, "init", self.codegen.get_cond_func)


def _builtin_cond_wait(self, args) -> ir.Value:
    """Wait on a condition variable. Requires cond handle and mutex handle.

    The mutex must be locked before calling. It is atomically unlocked
    during the wait and re-locked when signaled (Ada entry queue semantics).
    """
    if len(args) != 2:
        raise ExprGenError("cond_wait(cond, mutex) requires 2 arguments")
    void_ptr = ir.IntType(8).as_pointer()
    cond_handle = self.generate_expr(args[ARG_FIRST])
    mutex_handle = self.generate_expr(args[ARG_SECOND])
    cond_ptr = self.builder.inttoptr(cond_handle, void_ptr, name="cond_wptr")
    mtx_ptr = self.builder.inttoptr(mutex_handle, void_ptr, name="mtx_wptr")

    is_windows = (
        "windows" in self.codegen.module.triple.lower() or sys.platform == "win32"
    )
    wait_func = self.codegen.get_cond_func("wait")
    if is_windows:
        infinite = ir.Constant(ir.IntType(32), 0xFFFFFFFF)
        self.builder.call(wait_func, [cond_ptr, mtx_ptr, infinite])
    else:
        self.builder.call(wait_func, [cond_ptr, mtx_ptr])
    return ir.Constant(ir.IntType(64), 0)


def _builtin_cond_signal(self, args) -> ir.Value:
    """Signal one waiting thread on a condition variable."""
    return self._sync_call_void(
        args, "signal", self.codegen.get_cond_func, "cond_signal"
    )


def _builtin_cond_broadcast(self, args) -> ir.Value:
    """Signal all waiting threads on a condition variable."""
    return self._sync_call_void(
        args, "broadcast", self.codegen.get_cond_func, "cond_broadcast"
    )


def _builtin_cond_destroy(self, args) -> ir.Value:
    """Destroy a condition variable and free memory."""
    if len(args) != 1:
        raise ExprGenError("cond_destroy() requires 1 argument")
    void_ptr = ir.IntType(8).as_pointer()
    handle = self.generate_expr(args[ARG_FIRST])
    ptr = self.builder.inttoptr(handle, void_ptr, name="cond_dptr")
    destroy_func = self.codegen.get_cond_func("destroy")
    if destroy_func is not None:
        self.builder.call(destroy_func, [ptr])
    self.builder.call(self._sync_get_free(), [ptr])
    return ir.Constant(ir.IntType(64), 0)


def _builtin_rwlock_create(self, args) -> ir.Value:
    """Create a read-write lock. Returns handle (int).

    Ada equivalent: protected function (shared read) vs protected procedure (exclusive write)
    """
    if args:
        raise ExprGenError("rwlock_create() takes no arguments")
    return self._sync_alloc_and_init(256, "init", self.codegen.get_rwlock_func)


def _builtin_rwlock_read_lock(self, args) -> ir.Value:
    """Acquire shared read lock (multiple readers allowed)."""
    return self._sync_call_void(
        args, "read_lock", self.codegen.get_rwlock_func, "rwlock_read_lock"
    )


def _builtin_rwlock_write_lock(self, args) -> ir.Value:
    """Acquire exclusive write lock (blocks all other access)."""
    return self._sync_call_void(
        args, "write_lock", self.codegen.get_rwlock_func, "rwlock_write_lock"
    )


def _builtin_rwlock_read_unlock(self, args) -> ir.Value:
    """Release shared read lock."""
    return self._sync_call_void(
        args, "read_unlock", self.codegen.get_rwlock_func, "rwlock_read_unlock"
    )


def _builtin_rwlock_write_unlock(self, args) -> ir.Value:
    """Release exclusive write lock."""
    return self._sync_call_void(
        args, "write_unlock", self.codegen.get_rwlock_func, "rwlock_write_unlock"
    )


def _builtin_rwlock_destroy(self, args) -> ir.Value:
    """Destroy a read-write lock and free memory."""
    if len(args) != 1:
        raise ExprGenError("rwlock_destroy() requires 1 argument")
    void_ptr = ir.IntType(8).as_pointer()
    handle = self.generate_expr(args[ARG_FIRST])
    ptr = self.builder.inttoptr(handle, void_ptr, name="rwl_dptr")
    destroy_func = self.codegen.get_rwlock_func("destroy")
    if destroy_func is not None:
        self.builder.call(destroy_func, [ptr])
    self.builder.call(self._sync_get_free(), [ptr])
    return ir.Constant(ir.IntType(64), 0)


def _builtin_system(self, args) -> ir.Value:
    """Execute shell command: system(command) -> int (exit code)

    Uses the C library system() function.
    Returns the exit status of the command.

    Example: result = system("ls -la")
    """
    if len(args) != 1:
        raise ExprGenError("system() expects 1 argument (command string)")

    cmd_str = self.generate_expr(args[ARG_FIRST])

    # Declare system() from libc: int system(const char*)
    system_func = self.codegen.module.globals.get("system")
    if system_func is None:
        char_ptr = ir.IntType(8).as_pointer()
        system_ty = ir.FunctionType(ir.IntType(32), [char_ptr])
        system_func = ir.Function(self.codegen.module, system_ty, "system")

    result = self.builder.call(system_func, [cmd_str], name="system_result")

    # Sign-extend to i64
    return self.builder.sext(result, ir.IntType(64), name="system_i64")


# ------------------------------------------------------------------
# TCP sockets
# ------------------------------------------------------------------
# Five primitives mirror the C backend's runtime helpers, but emitted
# as LLVM IR via direct `declare` of the OS-level symbols. The
# platform divergence (Winsock2 SOCKET=u64, closesocket() vs POSIX
# int fd, close()) is handled the same way as make_dir / sleep_ms /
# time_ms above - branch on is_windows, declare the right symbol
# with the right signature.
#
# For the link side: ailang.py's compile_to_native scans the saved
# IR for a `@socket(` declaration and adds -lws2_32 when targeting
# Windows; POSIX gets sockets from libc with no extra flag.
