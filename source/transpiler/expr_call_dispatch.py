"""
Builtin-call dispatch table for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression
refactor.
"""

from __future__ import annotations

from typing import Any


class ExprBuiltinCallDispatcher:
    """Callable dispatch table for builtin functions.

    Delegates any helper attributes to the owning ``ExprGenerator`` via
    ``__getattr__`` to preserve existing proxy behavior.
    """

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen
        self._call_dispatch: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    def _get_call_dispatch(self) -> dict[str, Any]:
        """Lazily build the builtin function dispatch table."""

        if self._call_dispatch is not None:

            return self._call_dispatch

        cg = self.codegen

        # Math builtins using shared handler

        math_unary = {
            name: (lambda n, op=name: self._builtin_math_unary(n, op))
            for name in (
                "exp",
                "log",
                "sqrt",
                "sin",
                "cos",
                "tan",
                "tanh",
                "floor",
                "ceil",
                "fabs",
            )
        }

        # SIMD vec binary ops using shared handler

        vec_binops = {
            f"vec_{op}": (lambda n, op=op: self._builtin_vec_binop(n, op))
            for op in (
                "add",
                "sub",
                "mul",
                "and",
                "or",
                "xor",
                "nand",
                "nor",
                "xnor",
                "shr",
                "shl",
            )
        }

        # SIMD vec comparisons

        vec_cmps = {
            "vec_cmpeq": lambda n: self._builtin_vec_cmp(n, "=="),
            "vec_cmpgt": lambda n: self._builtin_vec_cmp(n, ">"),
            "vec_cmplt": lambda n: self._builtin_vec_cmp(n, "<"),
        }

        self._call_dispatch = {
            # I/O
            "print": lambda n: cg.builtin_print(n),
            "puts": lambda n: cg.builtin_print(n),
            "putc": lambda n: cg.builtin_putc(n),
            "input": self._builtin_input,
            "read_stdin": self._builtin_read_stdin,
            "read_file": self._builtin_read_file,
            "write_file": self._builtin_write_file,
            "file_size": self._builtin_file_size,
            "read_bytes": self._builtin_read_bytes,
            "write_bytes": self._builtin_write_bytes,
            "current_dir": self._builtin_current_dir,
            "change_dir": self._builtin_change_dir,
            # Core builtins
            "len": lambda n: self._dispatch_len(n),
            "hex": lambda n: self._dispatch_single_arg(
                n, "hex", cg.bigint_to_hex_string
            ),
            "bin": lambda n: self._dispatch_single_arg(
                n, "bin", cg.bigint_to_bin_string
            ),
            "oct": lambda n: self._dispatch_single_arg(
                n, "oct", cg.bigint_to_oct_string
            ),
            # Arrays
            "array_new": cg.builtin_array_new,
            "array_len": cg.builtin_array_len,
            "array_cap": cg.builtin_array_cap,
            "array_push": cg.builtin_array_push,
            "array_pop": cg.builtin_array_pop,
            "array_get": cg.builtin_array_get,
            "array_set": cg.builtin_array_set,
            "as_class": cg.builtin_as_class,
            # String arrays (i8* elements)
            "str_array_new": cg.builtin_str_array_new,
            "str_array_len": cg.builtin_str_array_len,
            "str_array_push": cg.builtin_str_array_push,
            "str_array_get": cg.builtin_str_array_get,
            "str_array_set": cg.builtin_str_array_set,
            "str_array_pop": cg.builtin_str_array_pop,
            "str_array_join": cg.builtin_str_array_join,
            "dealloc_str_array": cg.builtin_dealloc_str_array,
            # Strings
            "char_at": lambda n: cg.builtin_char_at(n),
            "unsafe_char_at": lambda n: cg.builtin_unsafe_char_at(n),
            "ord": lambda n: cg.builtin_ord(n),
            "chr": lambda n: cg.builtin_chr(n),
            "strlen": lambda n: cg.builtin_strlen(n),
            "str": lambda n: cg.builtin_str(n),
            "index_of": lambda n: cg.builtin_index_of(n),
            "index_of_from": lambda n: cg.builtin_index_of_from(n),
            "substr": lambda n: cg.builtin_substr(n),
            "concat": lambda n: cg.builtin_concat(n),
            "startswith": lambda n: cg.builtin_startswith(n),
            "endswith": lambda n: cg.builtin_endswith(n),
            "str_replace": lambda n: cg.builtin_str_replace(n),
            "streq": self._builtin_streq,
            "parse_int": self._builtin_parse_int,
            "split": self._builtin_split,
            "split_ints": self._builtin_split_ints,
            "split_len": self._builtin_split_len,
            "split_get": self._builtin_split_get,
            "split_str_get": self._builtin_split_str_get,
            "split_set": self._builtin_split_set,
            # Math (libm)
            **math_unary,
            "pow": self._builtin_pow,
            # Type introspection
            "typeof": self._builtin_typeof,
            "sizeof": self._builtin_sizeof,
            "alignof": self._builtin_alignof,
            "offsetof": self._builtin_offsetof,
            "target_os": self._builtin_target_os,
            "target_backend": self._builtin_target_backend,
            # SQL
            "sql_open": self._builtin_sql_open,
            "sql_open_readonly": self._builtin_sql_open_readonly,
            "sql_last_open_status": self._builtin_sql_last_open_status,
            "sql_exec": self._builtin_sql_exec,
            "sql_close": self._builtin_sql_close,
            "sql_prepare": self._builtin_sql_prepare,
            "sql_step": self._builtin_sql_step,
            "sql_bind_int": self._builtin_sql_bind_int,
            "sql_bind_text": self._builtin_sql_bind_text,
            "sql_bind_text_i64": self._builtin_sql_bind_text_i64,
            "sql_bind_text_i64_parts": self._builtin_sql_bind_text_i64_parts,
            "sql_bind_null": self._builtin_sql_bind_null,
            "sql_clear_bindings": self._builtin_sql_clear_bindings,
            "sql_reset": self._builtin_sql_reset,
            "sql_column_int": self._builtin_sql_column_int,
            "sql_column_text": self._builtin_sql_column_text,
            "sql_finalize": self._builtin_sql_finalize,
            # File operations (POSIX-named aliases ride the same impl)
            "file_exists": self._builtin_file_exists,
            "file_can_execute": self._builtin_file_can_execute,
            "access": self._builtin_access,
            "make_dir": self._builtin_make_dir,
            "mkdir": self._builtin_make_dir,
            "delete_file": self._builtin_delete_file,
            "unlink": self._builtin_delete_file,
            "move_file": self._builtin_move_file,
            "rename": self._builtin_move_file,
            "fd_open": self._builtin_fd_open,
            "fd_read": self._builtin_fd_read,
            "fd_write": self._builtin_fd_write,
            "fd_close": self._builtin_fd_close,
            "fd_dup": self._builtin_fd_dup,
            "fd_dup2": self._builtin_fd_dup2,
            "fd_tell": self._builtin_fd_tell,
            "fd_seek": self._builtin_fd_seek,
            "fd_flush": self._builtin_fd_flush,
            # Low-level I/O
            "poke": self._builtin_poke,
            "peek": self._builtin_peek,
            "outb": self._builtin_outb,
            "inb": self._builtin_inb,
            "ptr_add": self._builtin_ptr_add,
            "ptr_sub": self._builtin_ptr_sub,
            "peek64": self._builtin_peek64,
            "poke64": self._builtin_poke64,
            "peek32": self._builtin_peek32,
            "poke32": self._builtin_poke32,
            "peek8": self._builtin_peek8,
            "poke8": self._builtin_poke8,
            "addressof": self._builtin_addressof,
            "memcpy": self._builtin_memcpy,
            "memset": self._builtin_memset,
            "memmove": self._builtin_memmove,
            "stack_alloc": self._builtin_stack_alloc,
            "ptr_array": self._builtin_ptr_array,
            "realloc": self._builtin_realloc,
            "calloc": self._builtin_calloc,
            # Timing
            "time_ms": self._builtin_time_ms,
            "time_ns": self._builtin_time_ns,
            "rdtsc": self._builtin_rdtsc,
            "clock_ns": self._builtin_clock_ns,
            # SIMD - core
            "vec_load": self._builtin_vec_load,
            "vec_loadu": self._builtin_vec_loadu,
            "vec_store": self._builtin_vec_store,
            "vec_storeu": self._builtin_vec_storeu,
            "vec_broadcast": self._builtin_vec_broadcast,
            "vec_not": self._builtin_vec_not,
            "vec_movemask": self._builtin_vec_movemask,
            "vec_shuffle": self._builtin_vec_shuffle,
            "vec_extract": self._builtin_vec_extract,
            "vec_insert": self._builtin_vec_insert,
            # SIMD - binary ops and comparisons
            **vec_binops,
            **vec_cmps,
            # SIMD - SSE2+
            "vec_min": lambda n: self._builtin_vec_minmax(n, "min"),
            "vec_max": lambda n: self._builtin_vec_minmax(n, "max"),
            "vec_avg": self._builtin_vec_avg,
            "vec_sad": self._builtin_vec_sad,
            "vec_hadd": self._builtin_vec_hadd,
            "vec_shuffle_bytes": self._builtin_vec_shuffle_bytes,
            "vec_abs": self._builtin_vec_abs,
            "vec_blend": self._builtin_vec_blend,
            "vec_dot": self._builtin_vec_dot,
            "vec_cmpstr": self._builtin_vec_cmpstr,
            "vec_permute": self._builtin_vec_permute,
            "vec_gather": self._builtin_vec_gather,
            "vec_compress": self._builtin_vec_compress,
            "vec_expand": self._builtin_vec_expand,
            "vec_fma": self._builtin_vec_fma,
            "vec_fms": self._builtin_vec_fms,
            # Bit manipulation
            "popcount": self._builtin_popcount,
            "clz": self._builtin_clz,
            "ctz": self._builtin_ctz,
            # Threading
            "thread_id": self._builtin_thread_id,
            "num_cpus": self._builtin_num_cpus,
            "yield_thread": self._builtin_yield_thread,
            "sleep_ms": self._builtin_sleep_ms,
            # Synchronization primitives (Ada/SPARK-inspired)
            "mutex_create": self._builtin_mutex_create,
            "mutex_lock": self._builtin_mutex_lock,
            "mutex_unlock": self._builtin_mutex_unlock,
            "mutex_destroy": self._builtin_mutex_destroy,
            "cond_create": self._builtin_cond_create,
            "cond_wait": self._builtin_cond_wait,
            "cond_signal": self._builtin_cond_signal,
            "cond_broadcast": self._builtin_cond_broadcast,
            "cond_destroy": self._builtin_cond_destroy,
            "rwlock_create": self._builtin_rwlock_create,
            "rwlock_read_lock": self._builtin_rwlock_read_lock,
            "rwlock_write_lock": self._builtin_rwlock_write_lock,
            "rwlock_read_unlock": self._builtin_rwlock_read_unlock,
            "rwlock_write_unlock": self._builtin_rwlock_write_unlock,
            "rwlock_destroy": self._builtin_rwlock_destroy,
            # Atomics
            "atomic_load": self._builtin_atomic_load,
            "atomic_store": self._builtin_atomic_store,
            "atomic_add": self._builtin_atomic_add,
            "atomic_sub": self._builtin_atomic_sub,
            "atomic_exchange": self._builtin_atomic_exchange,
            "atomic_compare_exchange": self._builtin_atomic_cmpxchg,
            # System
            "system": self._builtin_system,
            "getpid": self._builtin_getpid,
            "getppid": self._builtin_getppid,
            "getuid": self._builtin_getuid,
            "geteuid": self._builtin_geteuid,
            "getgid": self._builtin_getgid,
            "getegid": self._builtin_getegid,
            "getgeid": self._builtin_getgeid,
            "process_umask": self._builtin_process_umask,
            "errno_get": self._builtin_errno_get,
            "errno_clear": self._builtin_errno_clear,
            "errno_set": self._builtin_errno_set,
            "syscall": self._builtin_syscall,
            "argc": self._builtin_argc,
            "argv": self._builtin_argv,
            # Win32 typed native helpers
            "win32_load_library": self._builtin_win32_load_library,
            "win32_get_proc_address": self._builtin_win32_get_proc_address,
            "win32_free_library": self._builtin_win32_free_library,
            "win32_get_last_error": self._builtin_win32_get_last_error,
            "win32_utf16_from_utf8": self._builtin_win32_utf16_from_utf8,
            "win32_full_path": self._builtin_win32_full_path,
            "win32_local_free": self._builtin_win32_local_free,
            "win32_shell_execute_runas": self._builtin_win32_shell_execute_runas,
            "win32_is_user_admin": self._builtin_win32_is_user_admin,
            "win32_hcs_vmcompute_available": self._builtin_win32_hcs_vmcompute_available,
            "win32_hcs_computecore_available": self._builtin_win32_hcs_computecore_available,
            "win32_hcs_open_compute_system": self._builtin_win32_hcs_open_compute_system,
            "win32_hcs_create_operation": self._builtin_win32_hcs_create_operation,
            "win32_hcs_close_operation": self._builtin_win32_hcs_close_operation,
            "win32_hcs_close_compute_system": self._builtin_win32_hcs_close_compute_system,
            "win32_hcs_wait_operation_result": self._builtin_win32_hcs_wait_operation_result,
            "win32_hcs_create_compute_system": self._builtin_win32_hcs_create_compute_system,
            "win32_hcs_start_compute_system": self._builtin_win32_hcs_start_compute_system,
            "win32_hcs_save_compute_system": self._builtin_win32_hcs_save_compute_system,
            "win32_hcs_shutdown_compute_system": self._builtin_win32_hcs_shutdown_compute_system,
            "win32_hcs_terminate_compute_system": self._builtin_win32_hcs_terminate_compute_system,
            "win32_hcs_get_compute_system_properties": self._builtin_win32_hcs_get_compute_system_properties,
            "win32_hcs_modify_compute_system": self._builtin_win32_hcs_modify_compute_system,
            # TCP sockets (Winsock2 on Windows, BSD sockets on POSIX
            # platform branching is internal to each _builtin_tcp_*)
            "tcp_connect": self._builtin_tcp_connect,
            "tcp_listen": self._builtin_tcp_listen,
            "tcp_accept": self._builtin_tcp_accept,
            "tcp_recv": self._builtin_tcp_recv,
            "tcp_send": self._builtin_tcp_send,
            "tcp_close": self._builtin_tcp_close,
            # Memory
            "alloc": self._builtin_alloc,
            "dealloc": self._builtin_dealloc,
            # Dictionaries
            "dict_new": self._builtin_dict_new,
            "dict_has_key": self._builtin_dict_has_key,
            "dict_size": self._builtin_dict_size,
            "dict_key_at": self._builtin_dict_key_at,
            "dict_value_at": self._builtin_dict_value_at,
            "dict_remove": self._builtin_dict_remove,
            "dict_get_type": self._builtin_dict_get_type,
            "dict_get_string": self._builtin_dict_get_string,
            # Function pointers
            "fn_ptr": lambda n: cg.builtin_fn_ptr(n),
            "fn_call": lambda n: cg.builtin_fn_call(n),
            "fn_call_str": lambda n: cg.builtin_fn_call_str(n),
            # Arena allocator (zero-overhead bump allocation)
            "arena_create": self._builtin_arena_create,
            "arena_alloc": self._builtin_arena_alloc,
            "arena_reset": self._builtin_arena_reset,
            "arena_destroy": self._builtin_arena_destroy,
            "arena_used": self._builtin_arena_used,
            "arena_remaining": self._builtin_arena_remaining,
            "arena_use": self._builtin_arena_use,
        }

        return self._call_dispatch
