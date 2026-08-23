"""Win32 helper-name registry for runtime-needs scanning."""

from __future__ import annotations

WIN32_HELPER_NAMES = (
    "win32_load_library",
    "win32_get_proc_address",
    "win32_free_library",
    "win32_get_last_error",
    "win32_utf16_from_utf8",
    "win32_full_path",
    "win32_local_free",
    "win32_shell_execute_runas",
    "win32_is_user_admin",
    "win32_hcs_vmcompute_available",
    "win32_hcs_computecore_available",
    "win32_hcs_open_compute_system",
    "win32_hcs_create_operation",
    "win32_hcs_close_operation",
    "win32_hcs_close_compute_system",
    "win32_hcs_wait_operation_result",
    "win32_hcs_create_compute_system",
    "win32_hcs_start_compute_system",
    "win32_hcs_save_compute_system",
    "win32_hcs_shutdown_compute_system",
    "win32_hcs_terminate_compute_system",
    "win32_hcs_get_compute_system_properties",
    "win32_hcs_modify_compute_system",
)
