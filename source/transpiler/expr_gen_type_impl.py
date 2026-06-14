"""Type-inference helpers for CExprEmitter."""

from __future__ import annotations

from parser import ast as A

from ast_access import arg_at


def _infer_vec_call_type(self, node: A.Call) -> str:
    """Infer return type for SIMD vector intrinsic calls."""
    if node.name in ("vec_extract", "vec_movemask"):
        return "int64_t"
    if node.args:
        last_arg = node.args[-1]
        if isinstance(last_arg, A.StringLit):
            if last_arg.value in ("vec32b", "vec256", "vec4l"):
                return "vec256"
            if last_arg.value in ("vec64b", "vec512", "vec8l"):
                return "vec512"
    for arg in node.args:
        if not isinstance(arg, A.Variable):
            continue
        func_scope = self.current_function
        if (
            func_scope in self._vec256_vars
            and arg.name in self._vec256_vars[func_scope]
        ):
            return "vec256"
        if None in self._vec256_vars and arg.name in self._vec256_vars[None]:
            return "vec256"
        if (
            func_scope in self._vec512_vars
            and arg.name in self._vec512_vars[func_scope]
        ):
            return "vec512"
        if None in self._vec512_vars and arg.name in self._vec512_vars[None]:
            return "vec512"
    return "vec128"


def _infer_type(self, node: A.ASTNode) -> str:
    """Infer C type from expression."""
    # Class instances are pointers.
    if isinstance(node, A.NewExpr) and node.type_name in self.classes:
        return f"{node.type_name} *"
    if isinstance(node, A.ReinterpretCast):
        return self._ailang_type_to_c(node.target_type)
    # Field access on a class pointer: look up the field's declared type.
    if isinstance(node, A.FieldAccess):
        parent_cls = self._class_ptr_type(node.object_expr)
        if parent_cls is None and isinstance(node.object_expr, A.ThisExpr):
            parent_cls = self._current_class
        if parent_cls is not None:
            ft = self._field_ailang_type(parent_cls, node.field_name)
            if ft is not None:
                return self._ailang_type_to_c(ft)
    if isinstance(node, A.StringLit):
        return "const char *"
    if isinstance(node, A.InterpolatedString):
        return "char *"
    if isinstance(node, A.StringSlice):
        return "char *"
    if isinstance(node, A.Number):
        if isinstance(node.value, float):
            return "double"
        return "int64_t"
    if isinstance(node, A.Bool):
        return "bool"
    if isinstance(node, A.ArrayLit):
        # Array literals - use ailang_array if tracking is available
        if self._needs_arrays:
            return "ailang_array"
        return "int64_t *"
    # Enum construction via MethodCall: EnumName.Variant(args)
    if isinstance(node, A.MethodCall) and isinstance(node.object_expr, A.Variable):
        enum_name = node.object_expr.name
        if enum_name in self.data_enums:
            return enum_name
    # Enum construction via EnumConstruct node
    if isinstance(node, A.EnumConstruct):
        return node.enum_name
    # Simple enum access or data enum simple variant: EnumName.Variant
    if isinstance(node, A.FieldAccess) and isinstance(node.object_expr, A.Variable):
        var_name = node.object_expr.name
        if var_name in self.data_enums:
            return var_name
    if isinstance(node, A.Variable):
        var_name = node.name
        # Check if variable has a tracked type (from parameter or declaration)
        if hasattr(self, "_var_types") and var_name in self._var_types:
            atype = self._var_types[var_name]
            # If it's a data enum, return the enum name directly
            if atype in self.data_enums:
                return atype
            # Otherwise convert to C type
            return self._ailang_type_to_c(atype)
        # Check if this variable is known to be a string
        if hasattr(self, "_string_vars"):
            func_scope = getattr(self, "current_function", None)
            if (
                func_scope in self._string_vars
                and var_name in self._string_vars[func_scope]
            ):
                return "const char *"
            if None in self._string_vars and var_name in self._string_vars[None]:
                return "const char *"
        # Check array vars
        if hasattr(self, "_array_vars") and var_name in self._array_vars:
            return "ailang_array"
        # Check dict vars
        if hasattr(self, "_dict_vars") and var_name in self._dict_vars:
            return "ailang_dict *"
        # Check dyn array vars
        if hasattr(self, "_dyn_array_vars") and var_name in self._dyn_array_vars:
            return "ailang_dyn_array"
        # Default to int64_t
        return "int64_t"
    if isinstance(node, A.Call):
        # SQLite handles travel as int64 throughout user code; the
        # call boundary inserts the cast bridges to/from sqlite3*.
        if node.name in ("sql_open", "sql_prepare"):
            return "int64_t"
        # String-returning helpers; keep this in sync with the dispatch.
        if node.name in self._STR_RETURNING_BUILTINS:
            return "const char *"
        # as_class(x, T) returns T pointer.
        if node.name == "as_class" and len(node.args) >= 2:
            tn = arg_at(node, 1)
            cls = None
            if isinstance(tn, A.StringLit) and tn.value in self.classes:
                cls = tn.value
            elif isinstance(tn, A.Variable) and tn.name in self.classes:
                cls = tn.name
            if cls:
                return f"{cls} *"
        # Split functions return struct types
        if node.name == "split_ints":
            return "IntArray"
        if node.name == "split":
            return "StringArray"
        # Dynamic array functions return ailang_dyn_array
        if node.name in ("array_new", "array_push", "array_set"):
            return "ailang_dyn_array"
        # String array functions return ailang_str_array
        if node.name in ("str_array_new", "str_array_push"):
            return "ailang_str_array"
        if node.name == "dict_new":
            return "ailang_dict *"
        if node.name.startswith("vec_"):
            return self._infer_vec_call_type(node)
        if node.name == "ctz":
            return "int64_t"
        if node.name in (
            "strlen",
            "char_at",
            "unsafe_char_at",
            "time_ns",
            "clock_ns",
            "abs",
            "min",
            "max",
            "int",
            "len",
            "index_of",
            "popcount",
        ):
            return "int64_t"
        if node.name in ("startswith", "endswith"):
            return "bool"
        if node.name in (
            "str",
            "ailang_strcat",
            "chr",
            "substr",
            "concat",
            "read_stdin",
            "read_file",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
            "input",
            "hex",
            "bin",
            "oct",
            "str_replace",
            "tcp_recv",
            "win32_full_path",
            "current_dir",
            "argv",
            "getenv",
            "typeof",
            "dict_get_type",
            "dict_get_string",
            "dict_key_at",
            "target_os",
            "target_backend",
        ):
            return "char *"
        if node.name in ("ptr_add", "ptr_sub", "rdtsc"):
            # AILang stores pointers as int64. ptr_add/ptr_sub do
            # byte arithmetic; rdtsc returns a tick count. Cast at
            # the call site so `q = ptr_add(p,8)` and `t = rdtsc()`
            # type-check.
            return "int64_t"
        if node.name in ("sqrt", "pow", "float"):
            return "double"
        # sql_open / sql_prepare handled earlier (returned as int64_t).
        if node.name == "sql_exec":
            return "int64_t"
        if node.name == "sql_step":
            return "int64_t"
        if node.name == "sql_bind_int":
            return "int64_t"
        if node.name == "sql_bind_text":
            return "int64_t"
        if node.name == "sql_bind_text_i64":
            return "int64_t"
        if node.name == "sql_bind_text_i64_parts":
            return "int64_t"
        if node.name == "sql_bind_null":
            return "int64_t"
        if node.name == "sql_clear_bindings":
            return "int64_t"
        if node.name == "sql_reset":
            return "int64_t"
        if node.name == "sql_column_int":
            return "int64_t"
        if node.name == "sql_column_text":
            return "const char *"
        if node.name == "sql_finalize":
            return "int64_t"
        if node.name == "syscall":
            return "int64_t"
        if node.name in (
            "getpid",
            "getppid",
            "getuid",
            "geteuid",
            "getgid",
            "getegid",
            "getgeid",
            "process_umask",
            "process_run_argv",
            "process_run_argv_redirs",
            "process_run_argv_env_redirs",
            "process_spawn_argv_env_redirs",
            "process_spawn_argv_env_redirs_pgrp",
            "process_wait_pid",
            "process_wait_pid_event",
            "process_poll_pid",
            "process_kill_pid",
            "process_get_pgrp",
            "process_set_pgrp",
            "process_kill_pgrp",
            "terminal_get_pgrp",
            "terminal_set_pgrp",
            "process_exec_replace_argv_env_redirs",
            "process_pipe_argv_redirs",
            "process_pipeline_argv_redirs",
            "process_pipeline_argv_env_redirs",
            "process_spawn_pipeline_argv_env_redirs",
            "process_spawn_pipeline_argv_env_redirs_pgrp",
            "process_last_capture_status",
            "process_set_last_capture_status",
            "process_last_exec_errno",
            "process_errno_enoexec",
            "process_errno_enoent",
            "process_errno_eacces",
            "process_errno_eperm",
            "signal_install",
            "signal_ignore",
            "signal_default",
            "signal_pending",
            "signal_clear",
            "signal_drain",
            "signal_raise",
        ):
            return "int64_t"
        if node.name in ("errno_get", "errno_clear", "errno_set"):
            return "int64_t"
        if node.name in (
            "fd_open",
            "fd_read",
            "fd_write",
            "fd_close",
            "fd_dup",
            "fd_dup2",
            "fd_tell",
            "fd_seek",
            "fd_flush",
        ):
            return "int64_t"
        if node.name == "file_exists":
            return "int64_t"
        if node.name == "file_can_execute":
            return "int64_t"
        if node.name in (
            "file_is_regular",
            "file_is_symlink",
            "file_is_block",
            "file_is_char",
            "file_is_fifo",
            "file_is_socket",
            "file_is_setuid",
            "file_is_setgid",
        ):
            return "int64_t"
        if node.name == "file_mtime":
            return "int64_t"
        if node.name == "file_same":
            return "int64_t"
        if node.name == "fd_is_tty":
            return "int64_t"
        if node.name == "change_dir":
            return "int64_t"
        if node.name == "access":
            return "int64_t"
        if node.name == "make_dir":
            return "int64_t"
        if node.name == "mkdir":
            return "int64_t"
        if node.name == "delete_file":
            return "int64_t"
        if node.name == "unlink":
            return "int64_t"
        if node.name == "move_file":
            return "int64_t"
        if node.name == "rename":
            return "int64_t"
        if node.name in (
            "tcp_connect",
            "tcp_listen",
            "tcp_accept",
            "tcp_send",
            "tcp_close",
            "win32_load_library",
            "win32_get_proc_address",
            "win32_free_library",
            "win32_get_last_error",
            "win32_utf16_from_utf8",
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
        ):
            return "int64_t"
        if node.name in self.functions:
            _, ret = self.functions[node.name]
            return self._ailang_type_to_c(ret)
    if isinstance(node, A.NewExpr):
        return node.type_name
    if (
        isinstance(node, A.BinaryOp)
        and node.op == "+"
        and (self._might_be_string(node.left) or self._might_be_string(node.right))
    ):
        return "char *"
    if isinstance(node, A.TernaryOp):
        true_type = self._infer_type(node.true_expr)
        false_type = self._infer_type(node.false_expr)
        if "char" in true_type or "char" in false_type:
            return "const char *"
        if true_type == "double" or false_type == "double":
            return "double"
        return true_type
    return "int64_t"


def _infer_typeof(self, node: A.ASTNode) -> str:
    """Infer AILang type name for typeof() - returns human-readable type name."""
    # Check variable types from declarations
    if isinstance(node, A.Variable):
        var_name = node.name
        # Check if we have type info from declarations
        if hasattr(self, "_var_types") and var_name in self._var_types:
            return self._var_types[var_name]
        # Check if it's a record
        if var_name in self.records:
            return var_name
        # Check if it's in string vars
        if var_name in self._string_vars:
            return "string"
    if isinstance(node, A.StringLit):
        return "string"
    if isinstance(node, A.Number):
        if isinstance(node.value, float):
            return "double"
        return "int"
    if isinstance(node, A.Bool):
        return "bool"
    if isinstance(node, A.ArrayLit):
        return "array"
    if isinstance(node, A.NewExpr):
        return node.type_name
    if isinstance(node, A.Call):
        # Certain builtins have known return types
        if node.name in ("strlen", "len", "ord", "index_of"):
            return "int"
        if node.name in ("char_at", "unsafe_char_at", "chr", "substr", "concat"):
            return "string"
        if node.name in ("typeof", "target_os", "target_backend"):
            return "string"
    return "int"
