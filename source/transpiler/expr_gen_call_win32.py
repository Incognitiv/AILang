"""C-backend Win32 builtin call mappings."""

from __future__ import annotations

from typing import Callable, Dict, List


def win32_c_builtin_mappings() -> Dict[str, Callable[[List[str]], str]]:
    """Return C expressions for typed Win32/HCS runtime helpers."""
    return {
        "win32_load_library": lambda a: f"ailang_win32_load_library({a[0]})",
        "win32_get_proc_address": lambda a: (
            f"ailang_win32_get_proc_address({a[0]}, {a[1]})"
        ),
        "win32_free_library": lambda a: f"ailang_win32_free_library({a[0]})",
        "win32_get_last_error": lambda a: "ailang_win32_get_last_error()",
        "win32_utf16_from_utf8": lambda a: f"ailang_win32_utf16_from_utf8({a[0]})",
        "win32_full_path": lambda a: f"ailang_win32_full_path({a[0]})",
        "win32_local_free": lambda a: f"ailang_win32_local_free({a[0]})",
        "win32_shell_execute_runas": lambda a: (
            f"ailang_win32_shell_execute_runas({a[0]}, {a[1]})"
        ),
        "win32_is_user_admin": lambda a: "ailang_win32_is_user_admin()",
        "win32_hcs_vmcompute_available": lambda a: (
            "ailang_win32_hcs_vmcompute_available()"
        ),
        "win32_hcs_computecore_available": lambda a: (
            "ailang_win32_hcs_computecore_available()"
        ),
        "win32_hcs_open_compute_system": lambda a: (
            f"ailang_win32_hcs_open_compute_system({a[0]}, {a[1]}, {a[2]})"
        ),
        "win32_hcs_create_operation": lambda a: "ailang_win32_hcs_create_operation()",
        "win32_hcs_close_operation": lambda a: (
            f"ailang_win32_hcs_close_operation({a[0]})"
        ),
        "win32_hcs_close_compute_system": lambda a: (
            f"ailang_win32_hcs_close_compute_system({a[0]})"
        ),
        "win32_hcs_wait_operation_result": lambda a: (
            f"ailang_win32_hcs_wait_operation_result({a[0]}, {a[1]}, {a[2]})"
        ),
        "win32_hcs_create_compute_system": lambda a: (
            f"ailang_win32_hcs_create_compute_system({a[0]}, {a[1]}, {a[2]}, {a[3]})"
        ),
        "win32_hcs_start_compute_system": lambda a: (
            f"ailang_win32_hcs_start_compute_system({a[0]}, {a[1]})"
        ),
        "win32_hcs_save_compute_system": lambda a: (
            f"ailang_win32_hcs_save_compute_system({a[0]}, {a[1]})"
        ),
        "win32_hcs_shutdown_compute_system": lambda a: (
            f"ailang_win32_hcs_shutdown_compute_system({a[0]}, {a[1]})"
        ),
        "win32_hcs_terminate_compute_system": lambda a: (
            f"ailang_win32_hcs_terminate_compute_system({a[0]}, {a[1]})"
        ),
        "win32_hcs_get_compute_system_properties": lambda a: (
            f"ailang_win32_hcs_get_compute_system_properties({a[0]}, {a[1]})"
        ),
        "win32_hcs_modify_compute_system": lambda a: (
            f"ailang_win32_hcs_modify_compute_system({a[0]}, {a[1]}, {a[2]})"
        ),
    }
