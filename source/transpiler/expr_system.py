"""System/builtin-utility methods for ``ExprGenerator``.

Extracted from ``emit_expressions.py`` as part of the LLVM expression
split.
"""

from __future__ import annotations

from typing import Any

from .expr_system_argv import _builtin_argc as _m_builtin_argc
from .expr_system_argv import _builtin_argv as _m_builtin_argv
from .expr_system_argv import _ensure_argv_globals as _m_ensure_argv_globals
from .expr_system_fd import _builtin_fd_close as _m_builtin_fd_close
from .expr_system_fd import _builtin_fd_dup as _m_builtin_fd_dup
from .expr_system_fd import _builtin_fd_dup2 as _m_builtin_fd_dup2
from .expr_system_fd import _builtin_fd_flush as _m_builtin_fd_flush
from .expr_system_fd import _builtin_fd_open as _m_builtin_fd_open
from .expr_system_fd import _builtin_fd_read as _m_builtin_fd_read
from .expr_system_fd import _builtin_fd_seek as _m_builtin_fd_seek
from .expr_system_fd import _builtin_fd_tell as _m_builtin_fd_tell
from .expr_system_fd import _builtin_fd_write as _m_builtin_fd_write
from .expr_system_lowlevel import _builtin_clz as _m_builtin_clz
from .expr_system_lowlevel import _builtin_ctz as _m_builtin_ctz
from .expr_system_lowlevel import _builtin_getpid as _m_builtin_getpid
from .expr_system_lowlevel import _builtin_inb as _m_builtin_inb
from .expr_system_lowlevel import _builtin_math_unary as _m_builtin_math_unary
from .expr_system_lowlevel import _builtin_outb as _m_builtin_outb
from .expr_system_lowlevel import _builtin_parse_int as _m_builtin_parse_int
from .expr_system_lowlevel import _builtin_peek as _m_builtin_peek
from .expr_system_lowlevel import _builtin_poke as _m_builtin_poke
from .expr_system_lowlevel import _builtin_popcount as _m_builtin_popcount
from .expr_system_lowlevel import _builtin_pow as _m_builtin_pow
from .expr_system_lowlevel import _builtin_ptr_add as _m_builtin_ptr_add
from .expr_system_lowlevel import _builtin_ptr_sub as _m_builtin_ptr_sub
from .expr_system_lowlevel import _builtin_rdtsc as _m_builtin_rdtsc
from .expr_system_lowlevel import _builtin_streq as _m_builtin_streq
from .expr_system_lowlevel import _builtin_syscall as _m_builtin_syscall
from .expr_system_process import _builtin_getegid as _m_builtin_getegid
from .expr_system_process import _builtin_geteuid as _m_builtin_geteuid
from .expr_system_process import _builtin_getgeid as _m_builtin_getgeid
from .expr_system_process import _builtin_getgid as _m_builtin_getgid
from .expr_system_process import _builtin_getppid as _m_builtin_getppid
from .expr_system_process import _builtin_getuid as _m_builtin_getuid
from .expr_system_process import _builtin_process_umask as _m_builtin_process_umask
from .expr_system_split import _begin_split_parse_loop as _m_begin_split_parse_loop
from .expr_system_split import _builtin_split as _m_builtin_split
from .expr_system_split import _builtin_split_get as _m_builtin_split_get
from .expr_system_split import _builtin_split_ints as _m_builtin_split_ints
from .expr_system_split import _builtin_split_len as _m_builtin_split_len
from .expr_system_split import _builtin_split_set as _m_builtin_split_set
from .expr_system_split import _builtin_split_str_get as _m_builtin_split_str_get
from .expr_system_split import (
    _continue_split_parse_loop as _m_continue_split_parse_loop,
)
from .expr_system_split import _create_split_common as _m_create_split_common
from .expr_system_split import _create_split_helper as _m_create_split_helper
from .expr_system_split import _create_split_ints_helper as _m_create_split_ints_helper
from .expr_system_split import _ensure_libc_functions as _m_ensure_libc_functions
from .expr_system_split import _finish_split_helper as _m_finish_split_helper
from .expr_system_status import _builtin_errno_clear as _m_builtin_errno_clear
from .expr_system_status import _builtin_errno_get as _m_builtin_errno_get
from .expr_system_status import _builtin_errno_set as _m_builtin_errno_set
from .expr_system_time import _builtin_clock_ns as _m_builtin_clock_ns
from .expr_system_time import _builtin_time_ms as _m_builtin_time_ms
from .expr_system_time import _builtin_time_ns as _m_builtin_time_ns
from .expr_system_time import _clock_ns_posix as _m_clock_ns_posix
from .expr_system_time import _clock_ns_windows as _m_clock_ns_windows
from .expr_system_time import _time_ms_linux as _m_time_ms_linux
from .expr_system_time import _time_ms_windows as _m_time_ms_windows
from .expr_system_time import _time_ns_linux as _m_time_ns_linux
from .expr_system_time import _time_ns_windows as _m_time_ns_windows
from .expr_system_win32 import (
    _builtin_win32_free_library as _m_builtin_win32_free_library,
)
from .expr_system_win32 import _builtin_win32_full_path as _m_builtin_win32_full_path
from .expr_system_win32 import (
    _builtin_win32_get_last_error as _m_builtin_win32_get_last_error,
)
from .expr_system_win32 import (
    _builtin_win32_get_proc_address as _m_builtin_win32_get_proc_address,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_close_compute_system as _m_builtin_win32_hcs_close_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_close_operation as _m_builtin_win32_hcs_close_operation,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_computecore_available as _m_builtin_win32_hcs_computecore_available,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_create_compute_system as _m_builtin_win32_hcs_create_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_create_operation as _m_builtin_win32_hcs_create_operation,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_get_compute_system_properties as _m_builtin_win32_hcs_get_compute_system_properties,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_modify_compute_system as _m_builtin_win32_hcs_modify_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_open_compute_system as _m_builtin_win32_hcs_open_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_save_compute_system as _m_builtin_win32_hcs_save_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_shutdown_compute_system as _m_builtin_win32_hcs_shutdown_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_start_compute_system as _m_builtin_win32_hcs_start_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_terminate_compute_system as _m_builtin_win32_hcs_terminate_compute_system,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_vmcompute_available as _m_builtin_win32_hcs_vmcompute_available,
)
from .expr_system_win32 import (
    _builtin_win32_hcs_wait_operation_result as _m_builtin_win32_hcs_wait_operation_result,
)
from .expr_system_win32 import (
    _builtin_win32_is_user_admin as _m_builtin_win32_is_user_admin,
)
from .expr_system_win32 import (
    _builtin_win32_load_library as _m_builtin_win32_load_library,
)
from .expr_system_win32 import _builtin_win32_local_free as _m_builtin_win32_local_free
from .expr_system_win32 import (
    _builtin_win32_shell_execute_runas as _m_builtin_win32_shell_execute_runas,
)
from .expr_system_win32 import (
    _builtin_win32_utf16_from_utf8 as _m_builtin_win32_utf16_from_utf8,
)
from .expr_system_win32 import (
    _emit_owned_empty_i8_string as _m_emit_owned_empty_i8_string,
)
from .expr_system_win32 import (
    _emit_win32_free_library_value as _m_emit_win32_free_library_value,
)
from .expr_system_win32 import _emit_win32_hcs_action3 as _m_emit_win32_hcs_action3
from .expr_system_win32 import _emit_win32_hcs_module as _m_emit_win32_hcs_module
from .expr_system_win32 import (
    _emit_win32_invoke_i32_proc as _m_emit_win32_invoke_i32_proc,
)
from .expr_system_win32 import (
    _emit_win32_invoke_ptr_proc as _m_emit_win32_invoke_ptr_proc,
)
from .expr_system_win32 import (
    _emit_win32_invoke_void_proc as _m_emit_win32_invoke_void_proc,
)
from .expr_system_win32 import (
    _emit_win32_load_library_name as _m_emit_win32_load_library_name,
)
from .expr_system_win32 import _emit_win32_proc_value as _m_emit_win32_proc_value
from .expr_system_win32 import (
    _emit_win32_utf16_from_utf8_value as _m_emit_win32_utf16_from_utf8_value,
)
from .expr_system_win32 import _win32_target_enabled as _m_win32_target_enabled


class ExprBuiltinSystemEmitter:
    """System and low-level builtin operations for ``ExprGenerator``."""

    def __init__(self, exprgen: Any) -> None:
        self._e = exprgen

    def __getattr__(self, name: str) -> Any:
        return getattr(self._e, name)

    _builtin_poke = _m_builtin_poke
    _builtin_peek = _m_builtin_peek
    _builtin_outb = _m_builtin_outb
    _builtin_inb = _m_builtin_inb
    _builtin_ptr_add = _m_builtin_ptr_add
    _builtin_ptr_sub = _m_builtin_ptr_sub
    _builtin_math_unary = _m_builtin_math_unary
    _builtin_pow = _m_builtin_pow
    _builtin_streq = _m_builtin_streq
    _builtin_parse_int = _m_builtin_parse_int
    _builtin_popcount = _m_builtin_popcount
    _builtin_clz = _m_builtin_clz
    _builtin_ctz = _m_builtin_ctz
    _builtin_rdtsc = _m_builtin_rdtsc
    _builtin_getpid = _m_builtin_getpid
    _builtin_getppid = _m_builtin_getppid
    _builtin_getuid = _m_builtin_getuid
    _builtin_geteuid = _m_builtin_geteuid
    _builtin_getgid = _m_builtin_getgid
    _builtin_getegid = _m_builtin_getegid
    _builtin_getgeid = _m_builtin_getgeid
    _builtin_process_umask = _m_builtin_process_umask
    _builtin_syscall = _m_builtin_syscall
    _builtin_errno_get = _m_builtin_errno_get
    _builtin_errno_clear = _m_builtin_errno_clear
    _builtin_errno_set = _m_builtin_errno_set
    _builtin_fd_open = _m_builtin_fd_open
    _builtin_fd_read = _m_builtin_fd_read
    _builtin_fd_write = _m_builtin_fd_write
    _builtin_fd_close = _m_builtin_fd_close
    _builtin_fd_dup = _m_builtin_fd_dup
    _builtin_fd_dup2 = _m_builtin_fd_dup2
    _builtin_fd_tell = _m_builtin_fd_tell
    _builtin_fd_seek = _m_builtin_fd_seek
    _builtin_fd_flush = _m_builtin_fd_flush

    _builtin_time_ms = _m_builtin_time_ms
    _time_ms_windows = _m_time_ms_windows
    _time_ms_linux = _m_time_ms_linux
    _builtin_time_ns = _m_builtin_time_ns
    _time_ns_windows = _m_time_ns_windows
    _time_ns_linux = _m_time_ns_linux
    _builtin_clock_ns = _m_builtin_clock_ns
    _clock_ns_windows = _m_clock_ns_windows
    _clock_ns_posix = _m_clock_ns_posix

    _builtin_split = _m_builtin_split
    _builtin_split_ints = _m_builtin_split_ints
    _builtin_split_len = _m_builtin_split_len
    _builtin_split_get = _m_builtin_split_get
    _builtin_split_str_get = _m_builtin_split_str_get
    _builtin_split_set = _m_builtin_split_set
    _ensure_libc_functions = _m_ensure_libc_functions
    _create_split_common = _m_create_split_common
    _begin_split_parse_loop = _m_begin_split_parse_loop
    _continue_split_parse_loop = _m_continue_split_parse_loop
    _finish_split_helper = _m_finish_split_helper
    _create_split_helper = _m_create_split_helper
    _create_split_ints_helper = _m_create_split_ints_helper

    _ensure_argv_globals = _m_ensure_argv_globals
    _builtin_argc = _m_builtin_argc
    _builtin_argv = _m_builtin_argv

    _win32_target_enabled = _m_win32_target_enabled
    _emit_win32_utf16_from_utf8_value = _m_emit_win32_utf16_from_utf8_value
    _emit_win32_load_library_name = _m_emit_win32_load_library_name
    _emit_win32_free_library_value = _m_emit_win32_free_library_value
    _emit_win32_proc_value = _m_emit_win32_proc_value
    _emit_win32_hcs_module = _m_emit_win32_hcs_module
    _emit_win32_invoke_i32_proc = _m_emit_win32_invoke_i32_proc
    _emit_win32_invoke_ptr_proc = _m_emit_win32_invoke_ptr_proc
    _emit_win32_invoke_void_proc = _m_emit_win32_invoke_void_proc
    _emit_win32_hcs_action3 = _m_emit_win32_hcs_action3
    _emit_owned_empty_i8_string = _m_emit_owned_empty_i8_string
    _builtin_win32_load_library = _m_builtin_win32_load_library
    _builtin_win32_get_proc_address = _m_builtin_win32_get_proc_address
    _builtin_win32_free_library = _m_builtin_win32_free_library
    _builtin_win32_get_last_error = _m_builtin_win32_get_last_error
    _builtin_win32_utf16_from_utf8 = _m_builtin_win32_utf16_from_utf8
    _builtin_win32_full_path = _m_builtin_win32_full_path
    _builtin_win32_shell_execute_runas = _m_builtin_win32_shell_execute_runas
    _builtin_win32_local_free = _m_builtin_win32_local_free
    _builtin_win32_is_user_admin = _m_builtin_win32_is_user_admin
    _builtin_win32_hcs_vmcompute_available = _m_builtin_win32_hcs_vmcompute_available
    _builtin_win32_hcs_computecore_available = (
        _m_builtin_win32_hcs_computecore_available
    )
    _builtin_win32_hcs_open_compute_system = _m_builtin_win32_hcs_open_compute_system
    _builtin_win32_hcs_create_operation = _m_builtin_win32_hcs_create_operation
    _builtin_win32_hcs_close_operation = _m_builtin_win32_hcs_close_operation
    _builtin_win32_hcs_close_compute_system = _m_builtin_win32_hcs_close_compute_system
    _builtin_win32_hcs_wait_operation_result = (
        _m_builtin_win32_hcs_wait_operation_result
    )
    _builtin_win32_hcs_create_compute_system = (
        _m_builtin_win32_hcs_create_compute_system
    )
    _builtin_win32_hcs_start_compute_system = _m_builtin_win32_hcs_start_compute_system
    _builtin_win32_hcs_save_compute_system = _m_builtin_win32_hcs_save_compute_system
    _builtin_win32_hcs_shutdown_compute_system = (
        _m_builtin_win32_hcs_shutdown_compute_system
    )
    _builtin_win32_hcs_terminate_compute_system = (
        _m_builtin_win32_hcs_terminate_compute_system
    )
    _builtin_win32_hcs_get_compute_system_properties = (
        _m_builtin_win32_hcs_get_compute_system_properties
    )
    _builtin_win32_hcs_modify_compute_system = (
        _m_builtin_win32_hcs_modify_compute_system
    )
