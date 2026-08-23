"""Threading and sync runtime declarations for LLVM codegen."""

from __future__ import annotations

from typing import Any

from llvmlite import ir


class RuntimeDeclsThreadSyncMixin:
    _cg: Any

    def get_pthread_create(self) -> ir.Function:
        """Lazy declaration of pthread_create (POSIX threads).

        ``int pthread_create(pthread_t *thread,
                             const pthread_attr_t *attr,
                             void *(*start_routine)(void*),
                             void *arg)``
        """
        if self._cg.pthread_create_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            pthread_t_ptr = ir.IntType(64).as_pointer()
            thread_func_ty = ir.FunctionType(void_ptr, [void_ptr])
            thread_func_ptr = thread_func_ty.as_pointer()

            pthread_create_ty = ir.FunctionType(
                ir.IntType(32),
                [pthread_t_ptr, void_ptr, thread_func_ptr, void_ptr],
            )
            self._cg.pthread_create_func = ir.Function(
                self._cg.module, pthread_create_ty, "pthread_create"
            )
        return self._cg.pthread_create_func

    def get_pthread_join(self) -> ir.Function:
        """Lazy declaration of pthread_join (POSIX threads).

        ``int pthread_join(pthread_t thread, void **retval)``
        """
        if self._cg.pthread_join_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            void_ptr_ptr = void_ptr.as_pointer()
            pthread_join_ty = ir.FunctionType(
                ir.IntType(32),
                [ir.IntType(64), void_ptr_ptr],
            )
            self._cg.pthread_join_func = ir.Function(
                self._cg.module, pthread_join_ty, "pthread_join"
            )
        return self._cg.pthread_join_func

    def get_create_thread(self) -> ir.Function:
        """Lazy declaration of Windows ``CreateThread``."""
        if self._cg.create_thread_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            thread_func_ty = ir.FunctionType(ir.IntType(32), [void_ptr])
            thread_func_ptr = thread_func_ty.as_pointer()
            create_thread_ty = ir.FunctionType(
                void_ptr,
                [
                    void_ptr,
                    ir.IntType(64),
                    thread_func_ptr,
                    void_ptr,
                    ir.IntType(32),
                    ir.IntType(32).as_pointer(),
                ],
            )
            self._cg.create_thread_func = ir.Function(
                self._cg.module, create_thread_ty, "CreateThread"
            )
        return self._cg.create_thread_func

    def get_wait_for_single_object(self) -> ir.Function:
        """Lazy declaration of Windows ``WaitForSingleObject``."""
        if self._cg.wait_for_single_object_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            wait_ty = ir.FunctionType(
                ir.IntType(32),
                [void_ptr, ir.IntType(32)],
            )
            self._cg.wait_for_single_object_func = ir.Function(
                self._cg.module, wait_ty, "WaitForSingleObject"
            )
        return self._cg.wait_for_single_object_func

    def get_close_handle(self) -> ir.Function:
        """Lazy declaration of Windows ``CloseHandle``."""
        if self._cg.close_handle_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            close_ty = ir.FunctionType(ir.IntType(32), [void_ptr])
            self._cg.close_handle_func = ir.Function(
                self._cg.module, close_ty, "CloseHandle"
            )
        return self._cg.close_handle_func

    def get_exit_code_thread(self) -> ir.Function:
        """Lazy declaration of Windows ``GetExitCodeThread``."""
        if self._cg.get_exit_code_thread_func is None:
            void_ptr = ir.IntType(8).as_pointer()
            get_exit_ty = ir.FunctionType(
                ir.IntType(32),
                [void_ptr, ir.IntType(32).as_pointer()],
            )
            self._cg.get_exit_code_thread_func = ir.Function(
                self._cg.module, get_exit_ty, "GetExitCodeThread"
            )
        return self._cg.get_exit_code_thread_func

    def _get_or_declare_exit(self) -> ir.Function:
        """Declare C ``exit()`` for program termination."""
        if self._cg.exit_func is None:
            exit_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
            self._cg.exit_func = ir.Function(self._cg.module, exit_ty, "exit")
        return self._cg.exit_func

    def get_exit_func(self) -> ir.Function:
        """Public accessor for ``exit()`` function declaration."""
        return self._get_or_declare_exit()

    def get_mutex_func(self, op: str) -> Any:
        """Get OS-level mutex function by operation name. Supports
        ``init`` / ``lock`` / ``unlock`` / ``destroy`` (Windows
        CRITICAL_SECTION or POSIX pthread_mutex)."""
        import sys as _sys

        if op in self._cg._mutex_funcs and self._cg._mutex_funcs[op] is not None:
            return self._cg._mutex_funcs[op]

        void_ptr = ir.IntType(8).as_pointer()
        is_windows = (
            "windows" in self._cg.module.triple.lower() or _sys.platform == "win32"
        )

        if is_windows:
            func_map = {
                "init": (
                    "InitializeCriticalSection",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "lock": (
                    "EnterCriticalSection",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "unlock": (
                    "LeaveCriticalSection",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "destroy": (
                    "DeleteCriticalSection",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
            }
        else:
            func_map = {
                "init": (
                    "pthread_mutex_init",
                    ir.FunctionType(ir.IntType(32), [void_ptr, void_ptr]),
                ),
                "lock": (
                    "pthread_mutex_lock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "unlock": (
                    "pthread_mutex_unlock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "destroy": (
                    "pthread_mutex_destroy",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
            }

        name, ftype = func_map[op]
        func = ir.Function(self._cg.module, ftype, name)
        self._cg._mutex_funcs[op] = func
        return func

    def get_cond_func(self, op: str) -> Any:
        """Get OS-level condition variable function by operation name.
        Supports ``init`` / ``wait`` / ``signal`` / ``broadcast`` /
        ``destroy``. Returns None for ``destroy`` on Windows because
        Windows condvars don't need destroying."""
        import sys as _sys

        if op in self._cg._cond_funcs and self._cg._cond_funcs[op] is not None:
            return self._cg._cond_funcs[op]

        void_ptr = ir.IntType(8).as_pointer()
        is_windows = (
            "windows" in self._cg.module.triple.lower() or _sys.platform == "win32"
        )

        if is_windows:
            func_map = {
                "init": (
                    "InitializeConditionVariable",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "wait": (
                    "SleepConditionVariableCS",
                    ir.FunctionType(
                        ir.IntType(32), [void_ptr, void_ptr, ir.IntType(32)]
                    ),
                ),
                "signal": (
                    "WakeConditionVariable",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "broadcast": (
                    "WakeAllConditionVariable",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "destroy": (None, None),
            }
        else:
            func_map = {
                "init": (
                    "pthread_cond_init",
                    ir.FunctionType(ir.IntType(32), [void_ptr, void_ptr]),
                ),
                "wait": (
                    "pthread_cond_wait",
                    ir.FunctionType(ir.IntType(32), [void_ptr, void_ptr]),
                ),
                "signal": (
                    "pthread_cond_signal",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "broadcast": (
                    "pthread_cond_broadcast",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "destroy": (
                    "pthread_cond_destroy",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
            }

        name, ftype = func_map[op]
        if name is None or ftype is None:
            self._cg._cond_funcs[op] = None
            return None
        func = ir.Function(self._cg.module, ftype, name)
        self._cg._cond_funcs[op] = func
        return func

    def get_rwlock_func(self, op: str) -> Any:
        """Get OS-level read-write lock function. Supports ``init`` /
        ``read_lock`` / ``write_lock`` / ``read_unlock`` /
        ``write_unlock`` / ``destroy`` (Windows SRWLOCK / POSIX
        pthread_rwlock). Returns None for ``destroy`` on Windows
        because SRWLOCKs don't need destroying."""
        import sys as _sys

        if op in self._cg._rwlock_funcs and self._cg._rwlock_funcs[op] is not None:
            return self._cg._rwlock_funcs[op]

        void_ptr = ir.IntType(8).as_pointer()
        is_windows = (
            "windows" in self._cg.module.triple.lower() or _sys.platform == "win32"
        )

        if is_windows:
            func_map = {
                "init": (
                    "InitializeSRWLock",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "read_lock": (
                    "AcquireSRWLockShared",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "write_lock": (
                    "AcquireSRWLockExclusive",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "read_unlock": (
                    "ReleaseSRWLockShared",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "write_unlock": (
                    "ReleaseSRWLockExclusive",
                    ir.FunctionType(ir.VoidType(), [void_ptr]),
                ),
                "destroy": (None, None),
            }
        else:
            func_map = {
                "init": (
                    "pthread_rwlock_init",
                    ir.FunctionType(ir.IntType(32), [void_ptr, void_ptr]),
                ),
                "read_lock": (
                    "pthread_rwlock_rdlock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "write_lock": (
                    "pthread_rwlock_wrlock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "read_unlock": (
                    "pthread_rwlock_unlock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "write_unlock": (
                    "pthread_rwlock_unlock",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
                "destroy": (
                    "pthread_rwlock_destroy",
                    ir.FunctionType(ir.IntType(32), [void_ptr]),
                ),
            }

        name, ftype = func_map[op]
        if name is None or ftype is None:
            self._cg._rwlock_funcs[op] = None
            return None
        func = ir.Function(self._cg.module, ftype, name)
        self._cg._rwlock_funcs[op] = func
        return func
