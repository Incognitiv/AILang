"""Runtime emitter helpers for Win32 dynamic-library handles."""

from __future__ import annotations

__all__ = ["emit_runtime_win32"]

from typing import Any


def emit_runtime_win32(emitter: Any) -> None:
    """Emit pointer-sized Win32 dynamic binding helpers.

    The public AILang builtins return int64 handles, but the C boundary casts
    through uintptr_t so HANDLE/HMODULE/FARPROC values are never truncated.
    Non-Windows targets emit stubs, which keeps portable programs buildable
    while still making platform absence explicit at runtime via 0 results.
    """
    if "win32" not in emitter._needs.helpers:
        return
    emitter._output.append("/* Win32 dynamic-library runtime helpers */")
    emitter._output.append(
        "#if defined(AILANG_WINDOWS) && !defined(AILANG_FREESTANDING)"
    )
    emitter._output.append("    #ifndef WIN32_LEAN_AND_MEAN")
    emitter._output.append("        #define WIN32_LEAN_AND_MEAN")
    emitter._output.append("    #endif")
    emitter._output.append("    #include <windows.h>")
    emitter._output.append("    #include <shellapi.h>")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_load_library(const char *name) {"
    )
    emitter._output.append("    if (!name) return 0;")
    emitter._output.append("    return (int64_t)(uintptr_t)LoadLibraryA(name);")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_get_proc_address("
        "int64_t module, const char *name) {"
    )
    emitter._output.append("    if (module == 0 || !name) return 0;")
    emitter._output.append(
        "    return (int64_t)(uintptr_t)GetProcAddress((HMODULE)(uintptr_t)module, name);"
    )
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_free_library(int64_t module) {"
    )
    emitter._output.append("    if (module == 0) return 0;")
    emitter._output.append(
        "    return FreeLibrary((HMODULE)(uintptr_t)module) ? 1 : 0;"
    )
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_get_last_error(void) {"
    )
    emitter._output.append("    return (int64_t)GetLastError();")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_utf16_from_utf8(const char *s) {"
    )
    emitter._output.append("    if (!s) return 0;")
    emitter._output.append(
        "    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);"
    )
    emitter._output.append("    if (n <= 0) return 0;")
    emitter._output.append(
        "    wchar_t *buf = (wchar_t *)ailang_safe_malloc((size_t)n * sizeof(wchar_t));"
    )
    emitter._output.append("    if (!buf) return 0;")
    emitter._output.append(
        "    if (MultiByteToWideChar(CP_UTF8, 0, s, -1, buf, n) <= 0) {"
    )
    emitter._output.append("        ailang_safe_free(buf);")
    emitter._output.append("        return 0;")
    emitter._output.append("    }")
    emitter._output.append("    return (int64_t)(uintptr_t)buf;")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_local_free(int64_t ptr) {"
    )
    emitter._output.append("    if (ptr == 0) return 1;")
    emitter._output.append(
        "    return LocalFree((HLOCAL)(uintptr_t)ptr) == NULL ? 1 : 0;"
    )
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static char *ailang_win32_owned_empty_string(void) {"
        " char *out = (char *)ailang_safe_malloc(1);"
        " out[0] = '\\0';"
        " return out; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static char *ailang_win32_full_path(const char *path) {"
    )
    emitter._output.append("    if (!path) return ailang_win32_owned_empty_string();")
    emitter._output.append("    DWORD need = GetFullPathNameA(path, 0, NULL, NULL);")
    emitter._output.append(
        "    if (need == 0) return ailang_win32_owned_empty_string();"
    )
    emitter._output.append(
        "    char *buf = (char *)ailang_safe_malloc((size_t)need + 1U);"
    )
    emitter._output.append("    buf[0] = '\\0';")
    emitter._output.append(
        "    DWORD written = GetFullPathNameA(path, need + 1U, buf, NULL);"
    )
    emitter._output.append(
        "    if (written == 0 || written > need) { buf[0] = '\\0'; }"
    )
    emitter._output.append("    return buf;")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_is_user_admin(void) {"
    )
    emitter._output.append('    HMODULE advapi = LoadLibraryA("advapi32.dll");')
    emitter._output.append("    if (!advapi) return 0;")
    emitter._output.append(
        "    typedef BOOL (WINAPI *ailang_open_process_token_fn)"
        "(HANDLE, DWORD, PHANDLE);"
    )
    emitter._output.append(
        "    typedef BOOL (WINAPI *ailang_get_token_information_fn)"
        "(HANDLE, TOKEN_INFORMATION_CLASS, LPVOID, DWORD, PDWORD);"
    )
    emitter._output.append(
        "    ailang_open_process_token_fn open_token = "
        "(ailang_open_process_token_fn)(uintptr_t)"
        'GetProcAddress(advapi, "OpenProcessToken");'
    )
    emitter._output.append(
        "    ailang_get_token_information_fn get_info = "
        "(ailang_get_token_information_fn)(uintptr_t)"
        'GetProcAddress(advapi, "GetTokenInformation");'
    )
    emitter._output.append(
        "    if (!open_token || !get_info) { FreeLibrary(advapi); return 0; }"
    )
    emitter._output.append("    HANDLE token = NULL;")
    emitter._output.append(
        "    if (!open_token(GetCurrentProcess(), TOKEN_QUERY, &token)) {"
        " FreeLibrary(advapi); return 0; }"
    )
    emitter._output.append("    TOKEN_ELEVATION elevation;")
    emitter._output.append("    DWORD returned = 0;")
    emitter._output.append(
        "    BOOL ok = get_info(token, TokenElevation, &elevation, "
        "sizeof(elevation), &returned);"
    )
    emitter._output.append("    CloseHandle(token);")
    emitter._output.append("    FreeLibrary(advapi);")
    emitter._output.append("    return (ok && elevation.TokenIsElevated) ? 1 : 0;")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_shell_execute_runas("
        "const char *exe, const char *params) {"
    )
    emitter._output.append("    if (!exe) return 0;")
    emitter._output.append('    HMODULE shell32 = LoadLibraryA("shell32.dll");')
    emitter._output.append("    if (!shell32) return 0;")
    emitter._output.append(
        "    typedef INT_PTR (WINAPI *ailang_shell_execute_w_fn)("
        "HWND, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, INT);"
    )
    emitter._output.append(
        "    ailang_shell_execute_w_fn shell_execute = "
        '(ailang_shell_execute_w_fn)(uintptr_t)GetProcAddress(shell32, "ShellExecuteW");'
    )
    emitter._output.append(
        "    if (!shell_execute) { FreeLibrary(shell32); return 0; }"
    )
    emitter._output.append(
        '    wchar_t *verb = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8("runas");'
    )
    emitter._output.append(
        "    wchar_t *file = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(exe);"
    )
    emitter._output.append(
        "    wchar_t *args = params ? (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(params) : NULL;"
    )
    emitter._output.append("    if (!verb || !file || (params && !args)) {")
    emitter._output.append("        if (verb) ailang_safe_free(verb);")
    emitter._output.append("        if (file) ailang_safe_free(file);")
    emitter._output.append("        if (args) ailang_safe_free(args);")
    emitter._output.append("        FreeLibrary(shell32);")
    emitter._output.append("        return 0;")
    emitter._output.append("    }")
    emitter._output.append(
        "    INT_PTR rc = shell_execute(NULL, verb, file, args, NULL, SW_SHOWNORMAL);"
    )
    emitter._output.append("    ailang_safe_free(verb);")
    emitter._output.append("    ailang_safe_free(file);")
    emitter._output.append("    if (args) ailang_safe_free(args);")
    emitter._output.append("    FreeLibrary(shell32);")
    emitter._output.append("    return (int64_t)rc;")
    emitter._output.append("}")
    emitter._output.append("typedef void *ailang_hcs_system_t;")
    emitter._output.append("typedef void *ailang_hcs_operation_t;")
    emitter._output.append(
        "AILANG_UNUSED static HMODULE ailang_win32_hcs_library(void) {"
        ' HMODULE h = LoadLibraryA("computecore.dll");'
        " if (h) return h;"
        ' return LoadLibraryA("vmcompute.dll"); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static FARPROC ailang_win32_hcs_proc(HMODULE h, const char *name) {"
        " return h ? GetProcAddress(h, name) : NULL; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_vmcompute_available(void) {"
        ' HMODULE h = LoadLibraryA("vmcompute.dll");'
        " if (!h) return 0;"
        ' FARPROC p = GetProcAddress(h, "HcsEnumerateComputeSystems");'
        " FreeLibrary(h);"
        " return p ? 1 : 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_computecore_available(void) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h) return 0;"
        ' FARPROC open_p = GetProcAddress(h, "HcsOpenComputeSystem");'
        ' FARPROC op_p = GetProcAddress(h, "HcsCreateOperation");'
        " FreeLibrary(h);"
        " return (open_p && op_p) ? 1 : 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_open_compute_system("
        "const char *name, int64_t access, int64_t system_slot) {"
    )
    emitter._output.append("    HMODULE h = ailang_win32_hcs_library();")
    emitter._output.append("    if (!h || !name || system_slot == 0) return -2;")
    emitter._output.append(
        "    typedef HRESULT (WINAPI *hcs_open_fn)(LPCWSTR, DWORD, ailang_hcs_system_t *);"
    )
    emitter._output.append(
        '    hcs_open_fn fn = (hcs_open_fn)(uintptr_t)ailang_win32_hcs_proc(h, "HcsOpenComputeSystem");'
    )
    emitter._output.append("    if (!fn) { FreeLibrary(h); return -2; }")
    emitter._output.append(
        "    wchar_t *wide = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(name);"
    )
    emitter._output.append("    if (!wide) { FreeLibrary(h); return -3; }")
    emitter._output.append("    ailang_hcs_system_t out = NULL;")
    emitter._output.append("    HRESULT hr = fn(wide, (DWORD)access, &out);")
    emitter._output.append("    ailang_safe_free(wide);")
    emitter._output.append("    *((void **)(uintptr_t)system_slot) = out;")
    emitter._output.append("    FreeLibrary(h);")
    emitter._output.append("    return (int64_t)hr;")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_create_operation(void) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h) return 0;"
        " typedef ailang_hcs_operation_t (WINAPI *hcs_create_operation_fn)(void *, void *);"
        " hcs_create_operation_fn fn = (hcs_create_operation_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsCreateOperation");'
        " ailang_hcs_operation_t op = fn ? fn(NULL, NULL) : NULL;"
        " FreeLibrary(h);"
        " return (int64_t)(uintptr_t)op; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_close_operation(int64_t operation) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h) return 0;"
        " typedef void (WINAPI *hcs_close_operation_fn)(ailang_hcs_operation_t);"
        " hcs_close_operation_fn fn = (hcs_close_operation_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsCloseOperation");'
        " if (fn && operation) fn((ailang_hcs_operation_t)(uintptr_t)operation);"
        " FreeLibrary(h);"
        " return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_close_compute_system(int64_t system_handle) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h) return 0;"
        " typedef void (WINAPI *hcs_close_system_fn)(ailang_hcs_system_t);"
        " hcs_close_system_fn fn = (hcs_close_system_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsCloseComputeSystem");'
        " if (fn && system_handle) fn((ailang_hcs_system_t)(uintptr_t)system_handle);"
        " FreeLibrary(h);"
        " return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_wait_operation_result("
        "int64_t operation, int64_t timeout_ms, int64_t result_slot) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h || !operation) return -4;"
        " typedef HRESULT (WINAPI *hcs_wait_fn)(ailang_hcs_operation_t, DWORD, PWSTR *);"
        " hcs_wait_fn fn = (hcs_wait_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsWaitForOperationResult");'
        " if (!fn) { FreeLibrary(h); return -4; }"
        " PWSTR result = NULL;"
        " HRESULT hr = fn((ailang_hcs_operation_t)(uintptr_t)operation, (DWORD)timeout_ms, &result);"
        " if (result) LocalFree(result);"
        " if (result_slot) *((void **)(uintptr_t)result_slot) = NULL;"
        " FreeLibrary(h);"
        " return (int64_t)hr; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_create_compute_system("
        "const char *name, const char *config, int64_t operation, int64_t system_slot) {"
    )
    emitter._output.append(
        "    HMODULE h = ailang_win32_hcs_library();"
        " if (!h || !name || !config || !operation || system_slot == 0) return -22;"
    )
    emitter._output.append(
        "    typedef HRESULT (WINAPI *hcs_create_system_fn)("
        "LPCWSTR, LPCWSTR, ailang_hcs_operation_t, void *, ailang_hcs_system_t *);"
    )
    emitter._output.append(
        "    hcs_create_system_fn fn = (hcs_create_system_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsCreateComputeSystem");'
    )
    emitter._output.append("    if (!fn) { FreeLibrary(h); return -22; }")
    emitter._output.append(
        "    wchar_t *wide_name = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(name);"
    )
    emitter._output.append(
        "    wchar_t *wide_config = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(config);"
    )
    emitter._output.append(
        "    if (!wide_name || !wide_config) {"
        " if (wide_name) ailang_safe_free(wide_name);"
        " if (wide_config) ailang_safe_free(wide_config);"
        " FreeLibrary(h); return -26; }"
    )
    emitter._output.append("    ailang_hcs_system_t out = NULL;")
    emitter._output.append(
        "    HRESULT hr = fn(wide_name, wide_config, "
        "(ailang_hcs_operation_t)(uintptr_t)operation, NULL, &out);"
    )
    emitter._output.append("    ailang_safe_free(wide_name);")
    emitter._output.append("    ailang_safe_free(wide_config);")
    emitter._output.append("    *((void **)(uintptr_t)system_slot) = out;")
    emitter._output.append("    FreeLibrary(h);")
    emitter._output.append("    return (int64_t)hr;")
    emitter._output.append("}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_action3("
        "const char *proc_name, int64_t system_handle, int64_t operation) {"
        " HMODULE h = ailang_win32_hcs_library();"
        " if (!h || !proc_name || !system_handle || !operation) return -14;"
        " typedef HRESULT (WINAPI *hcs_action_fn)(ailang_hcs_system_t, ailang_hcs_operation_t, LPCWSTR);"
        " hcs_action_fn fn = (hcs_action_fn)(uintptr_t)ailang_win32_hcs_proc(h, proc_name);"
        " if (!fn) { FreeLibrary(h); return -14; }"
        " HRESULT hr = fn((ailang_hcs_system_t)(uintptr_t)system_handle, "
        "(ailang_hcs_operation_t)(uintptr_t)operation, NULL);"
        " FreeLibrary(h);"
        " return (int64_t)hr; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_start_compute_system("
        "int64_t system_handle, int64_t operation) {"
        ' return ailang_win32_hcs_action3("HcsStartComputeSystem", system_handle, operation); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_save_compute_system("
        "int64_t system_handle, int64_t operation) {"
        ' return ailang_win32_hcs_action3("HcsSaveComputeSystem", system_handle, operation); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_shutdown_compute_system("
        "int64_t system_handle, int64_t operation) {"
        ' return ailang_win32_hcs_action3("HcsShutDownComputeSystem", system_handle, operation); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_terminate_compute_system("
        "int64_t system_handle, int64_t operation) {"
        ' return ailang_win32_hcs_action3("HcsTerminateComputeSystem", system_handle, operation); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_get_compute_system_properties("
        "int64_t system_handle, int64_t operation) {"
        ' return ailang_win32_hcs_action3("HcsGetComputeSystemProperties", system_handle, operation); }'
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_modify_compute_system("
        "int64_t system_handle, int64_t operation, const char *modify_doc) {"
    )
    emitter._output.append(
        "    HMODULE h = ailang_win32_hcs_library();"
        " if (!h || !system_handle || !operation || !modify_doc) return -31;"
    )
    emitter._output.append(
        "    typedef HRESULT (WINAPI *hcs_modify_fn)("
        "ailang_hcs_system_t, ailang_hcs_operation_t, LPCWSTR, HANDLE);"
    )
    emitter._output.append(
        "    hcs_modify_fn fn = (hcs_modify_fn)(uintptr_t)"
        'ailang_win32_hcs_proc(h, "HcsModifyComputeSystem");'
    )
    emitter._output.append("    if (!fn) { FreeLibrary(h); return -31; }")
    emitter._output.append(
        "    wchar_t *wide_doc = (wchar_t *)(uintptr_t)ailang_win32_utf16_from_utf8(modify_doc);"
    )
    emitter._output.append("    if (!wide_doc) { FreeLibrary(h); return -32; }")
    emitter._output.append(
        "    HRESULT hr = fn((ailang_hcs_system_t)(uintptr_t)system_handle, "
        "(ailang_hcs_operation_t)(uintptr_t)operation, wide_doc, NULL);"
    )
    emitter._output.append("    ailang_safe_free(wide_doc);")
    emitter._output.append("    FreeLibrary(h);")
    emitter._output.append("    return (int64_t)hr;")
    emitter._output.append("}")
    emitter._output.append("#else")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_load_library(const char *name) {"
        " (void)name; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_get_proc_address("
        "int64_t module, const char *name) {"
        " (void)module; (void)name; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_free_library(int64_t module) {"
        " (void)module; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_get_last_error(void) { return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_utf16_from_utf8(const char *s) {"
        " (void)s; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_local_free(int64_t ptr) {"
        " (void)ptr; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_shell_execute_runas("
        "const char *exe, const char *params) {"
        " (void)exe; (void)params; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static char *ailang_win32_full_path(const char *path) {"
        ' const char *src = path ? path : "";'
        " size_t n = strlen(src);"
        " char *out = (char *)ailang_safe_malloc(n + 1U);"
        " memcpy(out, src, n + 1U);"
        " return out; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_is_user_admin(void) { return 0; }"
    )
    for sig in (
        "ailang_win32_hcs_vmcompute_available(void)",
        "ailang_win32_hcs_computecore_available(void)",
        "ailang_win32_hcs_create_operation(void)",
    ):
        emitter._output.append(f"AILANG_UNUSED static int64_t {sig} {{ return 0; }}")
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_open_compute_system("
        "const char *name, int64_t access, int64_t system_slot) {"
        " (void)name; (void)access; (void)system_slot; return -2; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_close_operation(int64_t operation) {"
        " (void)operation; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_close_compute_system(int64_t system_handle) {"
        " (void)system_handle; return 0; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_wait_operation_result("
        "int64_t operation, int64_t timeout_ms, int64_t result_slot) {"
        " (void)operation; (void)timeout_ms; (void)result_slot; return -4; }"
    )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_create_compute_system("
        "const char *name, const char *config, int64_t operation, int64_t system_slot) {"
        " (void)name; (void)config; (void)operation; (void)system_slot; return -22; }"
    )
    for name in (
        "start",
        "save",
        "shutdown",
        "terminate",
        "get",
    ):
        c_name = (
            "get_compute_system_properties"
            if name == "get"
            else f"{name}_compute_system"
        )
        emitter._output.append(
            f"AILANG_UNUSED static int64_t ailang_win32_hcs_{c_name}("
            "int64_t system_handle, int64_t operation) {"
            " (void)system_handle; (void)operation; return -14; }"
        )
    emitter._output.append(
        "AILANG_UNUSED static int64_t ailang_win32_hcs_modify_compute_system("
        "int64_t system_handle, int64_t operation, const char *modify_doc) {"
        " (void)system_handle; (void)operation; (void)modify_doc; return -31; }"
    )
    emitter._output.append("#endif")
    emitter._output.append("")


_exported_emit_runtime_win32 = emit_runtime_win32
