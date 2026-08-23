"""Win32 dynamic-library builtins for LLVM expression generation."""

from __future__ import annotations

from llvmlite import ir
from transpiler.expr_common import ARG_FIRST, ARG_SECOND, ExprGenError


def _win32_target_enabled(self) -> bool:
    """Return true when the emitted LLVM target is Windows."""
    return "windows" in self.codegen.module.triple.lower()


def _builtin_win32_load_library(self, args) -> ir.Value:
    """win32_load_library(name) -> int64 HMODULE handle, or 0."""
    if len(args) != 1:
        raise ExprGenError("win32_load_library() expects (name)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    name = self.generate_expr(args[ARG_FIRST])
    i8_ptr = ir.IntType(8).as_pointer()
    load_ty = ir.FunctionType(i8_ptr, [i8_ptr])
    load_fn = self.codegen._declare_external("LoadLibraryA", load_ty)
    handle = self.builder.call(load_fn, [name], name="win32_hmodule")
    return self.builder.ptrtoint(handle, ir.IntType(64), name="win32_hmodule_i64")


def _builtin_win32_get_proc_address(self, args) -> ir.Value:
    """win32_get_proc_address(module, name) -> int64 FARPROC, or 0."""
    if len(args) != 2:
        raise ExprGenError("win32_get_proc_address() expects (module, name)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    module = self.ensure_int64(self.generate_expr(args[ARG_FIRST]))
    name = self.generate_expr(args[ARG_SECOND])
    i8_ptr = ir.IntType(8).as_pointer()
    module_ptr = self.builder.inttoptr(module, i8_ptr, name="win32_hmodule_ptr")
    proc_ty = ir.FunctionType(i8_ptr, [i8_ptr, i8_ptr])
    proc_fn = self.codegen._declare_external("GetProcAddress", proc_ty)
    proc = self.builder.call(proc_fn, [module_ptr, name], name="win32_proc")
    return self.builder.ptrtoint(proc, ir.IntType(64), name="win32_proc_i64")


def _builtin_win32_free_library(self, args) -> ir.Value:
    """win32_free_library(module) -> 1 on success, 0 on failure."""
    if len(args) != 1:
        raise ExprGenError("win32_free_library() expects (module)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    module = self.ensure_int64(self.generate_expr(args[ARG_FIRST]))
    i8_ptr = ir.IntType(8).as_pointer()
    module_ptr = self.builder.inttoptr(module, i8_ptr, name="win32_hmodule_ptr")
    free_ty = ir.FunctionType(ir.IntType(32), [i8_ptr])
    free_fn = self.codegen._declare_external("FreeLibrary", free_ty)
    rc = self.builder.call(free_fn, [module_ptr], name="win32_free_rc")
    return self.builder.sext(rc, ir.IntType(64), name="win32_free_i64")


def _builtin_win32_get_last_error(self, args) -> ir.Value:
    """win32_get_last_error() -> DWORD as int64."""
    if args:
        raise ExprGenError("win32_get_last_error() expects no arguments")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    err_ty = ir.FunctionType(ir.IntType(32), [])
    err_fn = self.codegen._declare_external("GetLastError", err_ty)
    err = self.builder.call(err_fn, [], name="win32_last_error")
    return self.builder.zext(err, ir.IntType(64), name="win32_last_error_i64")


def _emit_win32_utf16_from_utf8_value(self, s: ir.Value) -> ir.Value:
    """Allocate a UTF-16 copy of an i8* string and return it as i64."""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    i16_ptr = ir.IntType(16).as_pointer()
    i8_ptr = ir.IntType(8).as_pointer()
    mb_ty = ir.FunctionType(i32, [i32, i32, i8_ptr, i32, i16_ptr, i32])
    mb_fn = self.codegen._declare_external("MultiByteToWideChar", mb_ty)
    count = self.builder.call(
        mb_fn,
        [
            ir.Constant(i32, 65001),
            ir.Constant(i32, 0),
            s,
            ir.Constant(i32, -1),
            ir.Constant(i16_ptr, None),
            ir.Constant(i32, 0),
        ],
        name="win32_wide_count",
    )
    ok = self.builder.icmp_signed(">", count, ir.Constant(i32, 0), name="wide_ok")
    ok_block = self.codegen.current_function.append_basic_block("wide_alloc")
    fail_block = self.codegen.current_function.append_basic_block("wide_fail")
    merge_block = self.codegen.current_function.append_basic_block("wide_merge")
    self.builder.cbranch(ok, ok_block, fail_block)

    self.builder.position_at_end(fail_block)
    self.builder.branch(merge_block)
    fail_block = self.builder.block

    self.builder.position_at_end(ok_block)
    count64 = self.builder.zext(count, i64, name="wide_count64")
    bytes_needed = self.builder.mul(count64, ir.Constant(i64, 2), name="wide_bytes")
    raw = self.codegen.checked_malloc(bytes_needed, "win32_wide")
    wide = self.builder.bitcast(raw, i16_ptr, name="win32_wide_i16")
    self.builder.call(
        mb_fn,
        [
            ir.Constant(i32, 65001),
            ir.Constant(i32, 0),
            s,
            ir.Constant(i32, -1),
            wide,
            count,
        ],
    )
    raw_i64 = self.builder.ptrtoint(raw, i64, name="win32_wide_i64")
    self.builder.branch(merge_block)
    ok_block = self.builder.block

    self.builder.position_at_end(merge_block)
    result = self.builder.phi(i64, name="win32_wide_result")
    result.add_incoming(ir.Constant(i64, 0), fail_block)
    result.add_incoming(raw_i64, ok_block)
    return result


def _builtin_win32_utf16_from_utf8(self, args) -> ir.Value:
    """win32_utf16_from_utf8(s) -> heap-owned UTF-16 pointer as int64."""
    if len(args) != 1:
        raise ExprGenError("win32_utf16_from_utf8() expects (string)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    s = self.generate_expr(args[ARG_FIRST])
    return self._emit_win32_utf16_from_utf8_value(s)


def _builtin_win32_local_free(self, args) -> ir.Value:
    """win32_local_free(ptr) -> 1 on success, 0 on failure."""
    if len(args) != 1:
        raise ExprGenError("win32_local_free() expects (ptr)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    ptr_i64 = self.ensure_int64(self.generate_expr(args[ARG_FIRST]))
    i8_ptr = ir.IntType(8).as_pointer()
    ptr = self.builder.inttoptr(ptr_i64, i8_ptr, name="win32_local_ptr")
    free_ty = ir.FunctionType(i8_ptr, [i8_ptr])
    free_fn = self.codegen._declare_external("LocalFree", free_ty)
    result = self.builder.call(free_fn, [ptr], name="win32_local_free_result")
    ok = self.builder.icmp_unsigned(
        "==", result, ir.Constant(i8_ptr, None), name="win32_local_free_ok"
    )
    return self.builder.zext(ok, ir.IntType(64), name="win32_local_free_i64")


def _emit_owned_empty_i8_string(self) -> ir.Value:
    raw = self.codegen.checked_malloc(ir.Constant(ir.IntType(64), 1), "empty_str")
    self.builder.store(ir.Constant(ir.IntType(8), 0), raw)
    return raw


def _builtin_win32_full_path(self, args) -> ir.Value:
    """win32_full_path(path) -> heap-owned absolute Windows path string."""
    if len(args) != 1:
        raise ExprGenError("win32_full_path() expects (path)")
    path = self.generate_expr(args[ARG_FIRST])
    if not self._win32_target_enabled():
        empty = self.codegen.create_string_constant("")
        return self.codegen.generate_string_concat(path, empty)
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    i8_ptr = i8.as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    full_ty = ir.FunctionType(i32, [i8_ptr, i32, i8_ptr, i8_ptr_ptr])
    full_fn = self.codegen._declare_external("GetFullPathNameA", full_ty)
    need = self.builder.call(
        full_fn,
        [
            path,
            ir.Constant(i32, 0),
            ir.Constant(i8_ptr, None),
            ir.Constant(i8_ptr_ptr, None),
        ],
        name="full_path_need",
    )
    need64 = self.builder.zext(need, ir.IntType(64), name="full_path_need64")
    size = self.builder.add(
        need64, ir.Constant(ir.IntType(64), 1), name="full_path_size"
    )
    raw = self.codegen.checked_malloc(size, "full_path")
    self.builder.store(ir.Constant(i8, 0), raw)
    self.builder.call(
        full_fn,
        [
            path,
            self.builder.add(need, ir.Constant(i32, 1)),
            raw,
            ir.Constant(i8_ptr_ptr, None),
        ],
    )
    return raw


def _builtin_win32_shell_execute_runas(self, args) -> ir.Value:
    """win32_shell_execute_runas(exe, params) -> ShellExecuteW INT_PTR result."""
    if len(args) != 2:
        raise ExprGenError("win32_shell_execute_runas() expects (exe, params)")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    exe = self.generate_expr(args[ARG_FIRST])
    params = self.generate_expr(args[ARG_SECOND])
    i64 = ir.IntType(64)
    i32 = ir.IntType(32)
    i16_ptr = ir.IntType(16).as_pointer()
    i8_ptr = ir.IntType(8).as_pointer()

    shell32_name = self.codegen.create_string_constant("shell32.dll")
    load_ty = ir.FunctionType(i8_ptr, [i8_ptr])
    load_fn = self.codegen._declare_external("LoadLibraryA", load_ty)
    shell32 = self.builder.call(load_fn, [shell32_name], name="shell32_module")

    proc_name = self.codegen.create_string_constant("ShellExecuteW")
    proc_ty = ir.FunctionType(i8_ptr, [i8_ptr, i8_ptr])
    proc_fn = self.codegen._declare_external("GetProcAddress", proc_ty)
    shell_proc = self.builder.call(
        proc_fn, [shell32, proc_name], name="shell_execute_proc"
    )

    shell_ty = ir.FunctionType(i64, [i64, i16_ptr, i16_ptr, i16_ptr, i64, i32])
    shell_fn = self.builder.bitcast(
        shell_proc, shell_ty.as_pointer(), name="shell_execute_fn"
    )

    runas_text = self.codegen.create_string_constant("runas")
    verb = self._emit_win32_utf16_from_utf8_value(runas_text)
    wide_exe = self._emit_win32_utf16_from_utf8_value(exe)
    wide_params = self._emit_win32_utf16_from_utf8_value(params)
    verb_ptr = self.builder.inttoptr(verb, i16_ptr, name="runas_wide_ptr")
    exe_ptr = self.builder.inttoptr(wide_exe, i16_ptr, name="exe_wide_ptr")
    params_ptr = self.builder.inttoptr(wide_params, i16_ptr, name="params_wide_ptr")
    rc = self.builder.call(
        shell_fn,
        [
            ir.Constant(i64, 0),
            verb_ptr,
            exe_ptr,
            params_ptr,
            ir.Constant(i64, 0),
            ir.Constant(i32, 1),
        ],
        name="shell_execute_runas_rc",
    )

    i8_ptr = ir.IntType(8).as_pointer()
    free_fn = self.codegen._get_free()
    self.builder.call(free_fn, [self.builder.inttoptr(verb, i8_ptr)])
    self.builder.call(free_fn, [self.builder.inttoptr(wide_exe, i8_ptr)])
    self.builder.call(free_fn, [self.builder.inttoptr(wide_params, i8_ptr)])
    free_lib_ty = ir.FunctionType(i32, [i8_ptr])
    free_lib_fn = self.codegen._declare_external("FreeLibrary", free_lib_ty)
    self.builder.call(free_lib_fn, [shell32])
    return rc


def _emit_win32_load_library_name(self, dll_name: str) -> ir.Value:
    i8_ptr = ir.IntType(8).as_pointer()
    load_ty = ir.FunctionType(i8_ptr, [i8_ptr])
    load_fn = self.codegen._declare_external("LoadLibraryA", load_ty)
    return self.builder.call(
        load_fn,
        [self.codegen.create_string_constant(dll_name)],
        name="win32_module",
    )


def _emit_win32_free_library_value(self, module: ir.Value) -> None:
    i32 = ir.IntType(32)
    i8_ptr = ir.IntType(8).as_pointer()
    free_ty = ir.FunctionType(i32, [i8_ptr])
    free_fn = self.codegen._declare_external("FreeLibrary", free_ty)
    self.builder.call(free_fn, [module])


def _emit_win32_proc_value(self, module: ir.Value, proc_name: str) -> ir.Value:
    i8_ptr = ir.IntType(8).as_pointer()
    proc_ty = ir.FunctionType(i8_ptr, [i8_ptr, i8_ptr])
    proc_fn = self.codegen._declare_external("GetProcAddress", proc_ty)
    return self.builder.call(
        proc_fn,
        [module, self.codegen.create_string_constant(proc_name)],
        name="win32_proc",
    )


def _emit_win32_hcs_module(self) -> ir.Value:
    """Prefer computecore.dll and keep vmcompute.dll as a compatibility fallback."""
    # LLVM's direct codegen has no compact null-coalescing helper here. Use
    # computecore.dll for the professional HCS path; the C backend keeps the
    # fallback because it is easier to express safely there.
    return self._emit_win32_load_library_name("computecore.dll")


def _emit_win32_invoke_i32_proc(
    self, proc: ir.Value, arg_values: list[ir.Value], arg_types: list[ir.Type]
) -> ir.Value:
    i64 = ir.IntType(64)
    fn_ty = ir.FunctionType(ir.IntType(32), arg_types)
    fn_ptr = self.builder.bitcast(proc, fn_ty.as_pointer(), name="win32_typed_fn")
    rc = self.builder.call(fn_ptr, arg_values, name="win32_typed_rc")
    return self.builder.sext(rc, i64, name="win32_typed_i64")


def _emit_win32_invoke_ptr_proc(
    self, proc: ir.Value, arg_values: list[ir.Value], arg_types: list[ir.Type]
) -> ir.Value:
    i64 = ir.IntType(64)
    i8_ptr = ir.IntType(8).as_pointer()
    fn_ty = ir.FunctionType(i8_ptr, arg_types)
    fn_ptr = self.builder.bitcast(proc, fn_ty.as_pointer(), name="win32_typed_fn")
    rc = self.builder.call(fn_ptr, arg_values, name="win32_typed_ptr")
    return self.builder.ptrtoint(rc, i64, name="win32_typed_ptr_i64")


def _emit_win32_invoke_void_proc(
    self, proc: ir.Value, arg_values: list[ir.Value], arg_types: list[ir.Type]
) -> ir.Value:
    fn_ty = ir.FunctionType(ir.VoidType(), arg_types)
    fn_ptr = self.builder.bitcast(proc, fn_ty.as_pointer(), name="win32_typed_fn")
    self.builder.call(fn_ptr, arg_values)
    return ir.Constant(ir.IntType(64), 0)


def _builtin_win32_is_user_admin(self, args) -> ir.Value:
    if args:
        raise ExprGenError("win32_is_user_admin() expects no arguments")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    i8_ptr = ir.IntType(8).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    advapi = self._emit_win32_load_library_name("advapi32.dll")
    open_proc = self._emit_win32_proc_value(advapi, "OpenProcessToken")
    info_proc = self._emit_win32_proc_value(advapi, "GetTokenInformation")

    token_slot = self.builder.alloca(i8_ptr, name="token_slot")
    self.builder.store(ir.Constant(i8_ptr, None), token_slot)
    process = self.builder.inttoptr(
        ir.Constant(i64, -1), i8_ptr, name="current_process"
    )
    open_rc = self._emit_win32_invoke_i32_proc(
        open_proc,
        [process, ir.Constant(i32, 0x0008), token_slot],
        [i8_ptr, i32, i8_ptr_ptr],
    )
    open_ok = self.builder.icmp_signed(
        "!=", open_rc, ir.Constant(i64, 0), name="token_open_ok"
    )
    ok_block = self.function.append_basic_block("token_elevation_query")
    fail_block = self.function.append_basic_block("token_elevation_fail")
    merge_block = self.function.append_basic_block("token_elevation_merge")
    self.builder.cbranch(open_ok, ok_block, fail_block)

    self.builder.position_at_end(fail_block)
    self._emit_win32_free_library_value(advapi)
    self.builder.branch(merge_block)
    fail_end = self.builder.block

    self.builder.position_at_end(ok_block)
    token = self.builder.load(token_slot, name="token")
    elevation_slot = self.builder.alloca(i32, name="token_elevation")
    returned_slot = self.builder.alloca(i32, name="token_elevation_returned")
    elevation_ptr = self.builder.bitcast(elevation_slot, i8_ptr)
    get_rc = self._emit_win32_invoke_i32_proc(
        info_proc,
        [
            token,
            ir.Constant(i32, 20),  # TokenElevation
            elevation_ptr,
            ir.Constant(i32, 4),
            returned_slot,
        ],
        [i8_ptr, i32, i8_ptr, i32, i32.as_pointer()],
    )
    close_ty = ir.FunctionType(i32, [i8_ptr])
    close_fn = self.codegen._declare_external("CloseHandle", close_ty)
    self.builder.call(close_fn, [token])
    self._emit_win32_free_library_value(advapi)
    get_ok = self.builder.icmp_signed(
        "!=", get_rc, ir.Constant(i64, 0), name="token_info_ok"
    )
    elevated = self.builder.load(elevation_slot, name="token_is_elevated")
    elevated_ok = self.builder.icmp_signed(
        "!=", elevated, ir.Constant(i32, 0), name="token_elevated_nonzero"
    )
    result_ok = self.builder.and_(get_ok, elevated_ok, name="admin_result")
    result_i64 = self.builder.zext(result_ok, i64, name="admin_result_i64")
    self.builder.branch(merge_block)
    ok_end = self.builder.block

    self.builder.position_at_end(merge_block)
    result = self.builder.phi(i64, name="admin_result_phi")
    result.add_incoming(ir.Constant(i64, 0), fail_end)
    result.add_incoming(result_i64, ok_end)
    return result


def _builtin_win32_hcs_vmcompute_available(self, args) -> ir.Value:
    if args:
        raise ExprGenError("win32_hcs_vmcompute_available() expects no arguments")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    vmcompute = self._emit_win32_load_library_name("vmcompute.dll")
    proc = self._emit_win32_proc_value(vmcompute, "HcsEnumerateComputeSystems")
    proc_i64 = self.builder.ptrtoint(proc, ir.IntType(64), name="hcs_proc_i64")
    self._emit_win32_free_library_value(vmcompute)
    ok = self.builder.icmp_unsigned("!=", proc_i64, ir.Constant(ir.IntType(64), 0))
    return self.builder.zext(ok, ir.IntType(64), name="hcs_available")


def _builtin_win32_hcs_computecore_available(self, args) -> ir.Value:
    if args:
        raise ExprGenError("win32_hcs_computecore_available() expects no arguments")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsOpenComputeSystem")
    proc_i64 = self.builder.ptrtoint(proc, ir.IntType(64), name="hcs_proc_i64")
    self._emit_win32_free_library_value(hcs)
    ok = self.builder.icmp_unsigned("!=", proc_i64, ir.Constant(ir.IntType(64), 0))
    return self.builder.zext(ok, ir.IntType(64), name="hcs_available")


def _builtin_win32_hcs_create_operation(self, args) -> ir.Value:
    if args:
        raise ExprGenError("win32_hcs_create_operation() expects no arguments")
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    i8_ptr = ir.IntType(8).as_pointer()
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsCreateOperation")
    result = self._emit_win32_invoke_ptr_proc(
        proc,
        [ir.Constant(i8_ptr, None), ir.Constant(i8_ptr, None)],
        [i8_ptr, i8_ptr],
    )
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_close_operation(self, args) -> ir.Value:
    if len(args) != 1:
        raise ExprGenError("win32_hcs_close_operation() expects (operation)")
    (operation_arg,) = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    i8_ptr = ir.IntType(8).as_pointer()
    operation = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(operation_arg)), i8_ptr
    )
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsCloseOperation")
    result = self._emit_win32_invoke_void_proc(proc, [operation], [i8_ptr])
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_close_compute_system(self, args) -> ir.Value:
    if len(args) != 1:
        raise ExprGenError("win32_hcs_close_compute_system() expects (system)")
    (system_arg,) = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), 0)
    i8_ptr = ir.IntType(8).as_pointer()
    system = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(system_arg)), i8_ptr
    )
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsCloseComputeSystem")
    result = self._emit_win32_invoke_void_proc(proc, [system], [i8_ptr])
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_open_compute_system(self, args) -> ir.Value:
    if len(args) != 3:
        raise ExprGenError(
            "win32_hcs_open_compute_system() expects (name, access, system_slot)"
        )
    name_arg, access_arg, slot_arg = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), -2)
    i8_ptr = ir.IntType(8).as_pointer()
    i16_ptr = ir.IntType(16).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    name = self.generate_expr(name_arg)
    access = self.builder.trunc(
        self.ensure_int64(self.generate_expr(access_arg)), ir.IntType(32)
    )
    slot = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(slot_arg)), i8_ptr_ptr
    )
    wide_name_i64 = self._emit_win32_utf16_from_utf8_value(name)
    wide_name = self.builder.inttoptr(wide_name_i64, i16_ptr)
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsOpenComputeSystem")
    result = self._emit_win32_invoke_i32_proc(
        proc, [wide_name, access, slot], [i16_ptr, ir.IntType(32), i8_ptr_ptr]
    )
    free_fn = self.codegen._get_free()
    self.builder.call(free_fn, [self.builder.inttoptr(wide_name_i64, i8_ptr)])
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_wait_operation_result(self, args) -> ir.Value:
    if len(args) != 3:
        raise ExprGenError(
            "win32_hcs_wait_operation_result() expects (operation, timeout_ms, result_slot)"
        )
    operation_arg, timeout_arg, _result_slot_arg = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), -4)
    i8_ptr = ir.IntType(8).as_pointer()
    i16_ptr = ir.IntType(16).as_pointer()
    i16_ptr_ptr = i16_ptr.as_pointer()
    operation = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(operation_arg)), i8_ptr
    )
    timeout = self.builder.trunc(
        self.ensure_int64(self.generate_expr(timeout_arg)), ir.IntType(32)
    )
    result_slot = self.builder.alloca(i16_ptr, name="hcs_result_slot")
    self.builder.store(ir.Constant(i16_ptr, None), result_slot)
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsWaitForOperationResult")
    result = self._emit_win32_invoke_i32_proc(
        proc, [operation, timeout, result_slot], [i8_ptr, ir.IntType(32), i16_ptr_ptr]
    )
    local_free_ty = ir.FunctionType(i8_ptr, [i8_ptr])
    local_free = self.codegen._declare_external("LocalFree", local_free_ty)
    result_ptr = self.builder.load(result_slot)
    self.builder.call(local_free, [self.builder.bitcast(result_ptr, i8_ptr)])
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_create_compute_system(self, args) -> ir.Value:
    if len(args) != 4:
        raise ExprGenError(
            "win32_hcs_create_compute_system() expects (name, config, operation, system_slot)"
        )
    name_arg, config_arg, operation_arg, slot_arg = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), -22)
    i8_ptr = ir.IntType(8).as_pointer()
    i16_ptr = ir.IntType(16).as_pointer()
    i8_ptr_ptr = i8_ptr.as_pointer()
    name = self.generate_expr(name_arg)
    config = self.generate_expr(config_arg)
    operation = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(operation_arg)), i8_ptr
    )
    slot = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(slot_arg)), i8_ptr_ptr
    )
    wide_name_i64 = self._emit_win32_utf16_from_utf8_value(name)
    wide_config_i64 = self._emit_win32_utf16_from_utf8_value(config)
    wide_name = self.builder.inttoptr(wide_name_i64, i16_ptr)
    wide_config = self.builder.inttoptr(wide_config_i64, i16_ptr)
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsCreateComputeSystem")
    result = self._emit_win32_invoke_i32_proc(
        proc,
        [wide_name, wide_config, operation, ir.Constant(i8_ptr, None), slot],
        [i16_ptr, i16_ptr, i8_ptr, i8_ptr, i8_ptr_ptr],
    )
    free_fn = self.codegen._get_free()
    self.builder.call(free_fn, [self.builder.inttoptr(wide_name_i64, i8_ptr)])
    self.builder.call(free_fn, [self.builder.inttoptr(wide_config_i64, i8_ptr)])
    self._emit_win32_free_library_value(hcs)
    return result


def _emit_win32_hcs_action3(self, proc_name: str, args) -> ir.Value:
    if len(args) != 2:
        raise ExprGenError(f"{proc_name} wrapper expects (system, operation)")
    system_arg, operation_arg = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), -14)
    i8_ptr = ir.IntType(8).as_pointer()
    i16_ptr = ir.IntType(16).as_pointer()
    system = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(system_arg)), i8_ptr
    )
    operation = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(operation_arg)), i8_ptr
    )
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, proc_name)
    result = self._emit_win32_invoke_i32_proc(
        proc,
        [system, operation, ir.Constant(i16_ptr, None)],
        [i8_ptr, i8_ptr, i16_ptr],
    )
    self._emit_win32_free_library_value(hcs)
    return result


def _builtin_win32_hcs_start_compute_system(self, args) -> ir.Value:
    return self._emit_win32_hcs_action3("HcsStartComputeSystem", args)


def _builtin_win32_hcs_save_compute_system(self, args) -> ir.Value:
    return self._emit_win32_hcs_action3("HcsSaveComputeSystem", args)


def _builtin_win32_hcs_shutdown_compute_system(self, args) -> ir.Value:
    return self._emit_win32_hcs_action3("HcsShutDownComputeSystem", args)


def _builtin_win32_hcs_terminate_compute_system(self, args) -> ir.Value:
    return self._emit_win32_hcs_action3("HcsTerminateComputeSystem", args)


def _builtin_win32_hcs_get_compute_system_properties(self, args) -> ir.Value:
    return self._emit_win32_hcs_action3("HcsGetComputeSystemProperties", args)


def _builtin_win32_hcs_modify_compute_system(self, args) -> ir.Value:
    if len(args) != 3:
        raise ExprGenError(
            "win32_hcs_modify_compute_system() expects (system, operation, modify_doc)"
        )
    system_arg, operation_arg, doc_arg = args
    if not self._win32_target_enabled():
        return ir.Constant(ir.IntType(64), -31)
    i8_ptr = ir.IntType(8).as_pointer()
    i16_ptr = ir.IntType(16).as_pointer()
    system = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(system_arg)), i8_ptr
    )
    operation = self.builder.inttoptr(
        self.ensure_int64(self.generate_expr(operation_arg)), i8_ptr
    )
    doc = self.generate_expr(doc_arg)
    wide_doc_i64 = self._emit_win32_utf16_from_utf8_value(doc)
    wide_doc = self.builder.inttoptr(wide_doc_i64, i16_ptr)
    hcs = self._emit_win32_hcs_module()
    proc = self._emit_win32_proc_value(hcs, "HcsModifyComputeSystem")
    result = self._emit_win32_invoke_i32_proc(
        proc,
        [system, operation, wide_doc, ir.Constant(i8_ptr, None)],
        [i8_ptr, i8_ptr, i16_ptr, i8_ptr],
    )
    free_fn = self.codegen._get_free()
    self.builder.call(free_fn, [self.builder.inttoptr(wide_doc_i64, i8_ptr)])
    self._emit_win32_free_library_value(hcs)
    return result
