"""Call-generation implementation for CExprEmitter."""

from __future__ import annotations

from parser import ast as A

from ast_access import arg_at
from callback_types import callback_parts, resolve_callback_alias
from target_info import os_from_platform
from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.expr_gen_call_builtin_map import c_builtin_mappings


def _generate_call(self, node: A.Call) -> str:
    """Generate function call expression."""
    # AILang's `putc(c)` is the documented "print one character"
    # builtin (single-arg). C's libc `putc(c, FILE*)` takes two
    # args, so a literal passthrough fails to compile. Re-route to
    # `putchar(c)` which has the right shape.
    if node.name == "putc" and len(node.args) == 1:
        return f"(int64_t)putchar((int){self.expr(arg_at(node, 0))})"
    if node.name == "target_os" and not node.args:
        return f'"{os_from_platform()}"'
    if node.name == "target_backend" and not node.args:
        return '"c"'
    if node.name == "offsetof" and node.name not in self.user_defined_funcs:
        if len(node.args) != 2:
            raise ValueError("offsetof() expects exactly 2 arguments")
        type_arg, field_arg = node.args
        if not isinstance(type_arg, A.StringLit) or not isinstance(
            field_arg, A.StringLit
        ):
            raise ValueError(
                'offsetof() expects string literals: offsetof("Type", "field")'
            )
        return self._emit_offsetof(type_arg.value, field_arg.value)
    # SQLite handles MUST flow through user code as int64_t (since
    # AILang has no native pointer-typed handles); the C runtime
    # functions take/return real `sqlite3 *` / `sqlite3_stmt *`.
    # Bridge in both directions at the call boundary so user code
    # never has to know the difference.
    if node.name == "sql_open" and len(node.args) >= 1:
        return f"((int64_t)(uintptr_t)sql_open({self.expr(arg_at(node, 0))}))"
    if node.name == "sql_exec" and len(node.args) >= 2:
        db = self.expr(arg_at(node, 0))
        sql = self.expr(arg_at(node, 1))
        return f"sql_exec((sqlite3 *)(uintptr_t)({db}), {sql})"
    if node.name == "sql_close" and len(node.args) >= 1:
        db = self.expr(arg_at(node, 0))
        # sql_close returns void; emit the bare call so it works as a
        # statement under -Werror=unused-value. Expression-context use
        # would (correctly) fail since you can't take the value of a
        # void call.
        return f"sql_close((sqlite3 *)(uintptr_t)({db}))"
    if node.name == "sql_prepare" and len(node.args) >= 2:
        db = self.expr(arg_at(node, 0))
        sql = self.expr(arg_at(node, 1))
        return (
            f"((int64_t)(uintptr_t)sql_prepare("
            f"(sqlite3 *)(uintptr_t)({db}), {sql}))"
        )
    if node.name == "sql_step" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_step((sqlite3_stmt *)(uintptr_t)({stmt}))"
    if node.name == "sql_bind_int" and len(node.args) >= 3:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        val = self.expr(arg_at(node, 2))
        return f"sql_bind_int((sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {val})"
    if node.name == "sql_bind_text" and len(node.args) >= 3:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        val = self.expr(arg_at(node, 2))
        return f"sql_bind_text((sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {val})"
    if node.name == "sql_bind_text_i64" and len(node.args) >= 4:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        prefix = self.expr(arg_at(node, 2))
        val = self.expr(node.args[3])
        return (
            "sql_bind_text_i64("
            f"(sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {prefix}, {val})"
        )
    if node.name == "sql_bind_text_i64_parts" and len(node.args) >= 5:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        prefix = self.expr(arg_at(node, 2))
        val = self.expr(node.args[3])
        suffix = self.expr(node.args[4])
        return (
            "sql_bind_text_i64_parts("
            f"(sqlite3_stmt *)(uintptr_t)({stmt}), {idx}, {prefix}, {val}, {suffix})"
        )
    if node.name == "sql_bind_null" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        idx = self.expr(arg_at(node, 1))
        return f"sql_bind_null((sqlite3_stmt *)(uintptr_t)({stmt}), {idx})"
    if node.name == "sql_clear_bindings" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_clear_bindings((sqlite3_stmt *)(uintptr_t)({stmt}))"
    if node.name == "sql_finalize" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"sql_finalize((sqlite3_stmt *)(uintptr_t)({stmt}))"
    # sql_reset: rewind a prepared statement so it can be re-executed
    # without re-parsing the SQL. Critical for hot paths — the
    # alternative is sql_prepare per request, which is ~20% of CPU
    # in adapt_serve's /api/adapt/status hot loop (sqlite3Parser /
    # sqlite3Select dominate). With cached statements + sql_reset,
    # the parse cost is paid once at server startup.
    if node.name == "sql_reset" and len(node.args) >= 1:
        stmt = self.expr(arg_at(node, 0))
        return f"((int64_t)sqlite3_reset(" f"(sqlite3_stmt *)(uintptr_t)({stmt})))"
    if node.name == "sql_column_int" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        col = self.expr(arg_at(node, 1))
        return f"sql_column_int((sqlite3_stmt *)(uintptr_t)({stmt}), {col})"
    if node.name == "sql_column_text" and len(node.args) >= 2:
        stmt = self.expr(arg_at(node, 0))
        col = self.expr(arg_at(node, 1))
        return f"sql_column_text((sqlite3_stmt *)(uintptr_t)({stmt}), {col})"

    # Class instances are pointers; the dynamic-array runtime stores
    # int64_t. Cast the pointer via uintptr_t so we don't lose bits.
    if node.name in ("array_push", "array_set") and len(node.args) >= 2:
        val_idx = 1 if node.name == "array_push" else 2
        val_node = node.args[val_idx]
        if self._class_ptr_type(val_node) is not None:
            val_c = self.expr(val_node)
            arr_c = self.expr(arg_at(node, 0))
            if node.name == "array_push":
                return f"array_push({arr_c}, (int64_t)(uintptr_t)({val_c}))"
            idx_c = self.expr(arg_at(node, 1))
            return f"array_set({arr_c}, {idx_c}, (int64_t)(uintptr_t)({val_c}))"

    if node.name == "array_get" and len(node.args) >= 2:
        arr_node = arg_at(node, 0)
        idx_node = arg_at(node, 1)
        if isinstance(arr_node, A.FieldAccess) and isinstance(idx_node, A.Number):
            owner = None
            if isinstance(arr_node.object_expr, A.Variable):
                owner = arr_node.object_expr.name
            elif isinstance(arr_node.object_expr, A.ThisExpr):
                owner = getattr(self, "_inline_this_stack_var", None)
            values = getattr(self, "_stack_array_field_values", {}).get(
                (owner, arr_node.field_name)
            )
            if values is not None and not idx_node.is_float:
                index_value = int(idx_node.value)
                if 0 <= index_value < len(values):
                    return values[index_value]
        if isinstance(arr_node, (A.Variable, A.FieldAccess)) and isinstance(
            idx_node, A.Number
        ):
            arr_c = self.expr(arr_node)
            idx_c = self.expr(idx_node)
            return (
                f"(({idx_c} < 0 || {idx_c} >= {arr_c}.length) "
                f"? 0 : {arr_c}.data[{idx_c}])"
            )

    if node.name == "streq" and node.name not in self.user_defined_funcs:
        from transpiler.expr_string_fastpath import emit_streq_literal_fastpath

        fast_streq = emit_streq_literal_fastpath(self, node)
        if fast_streq is not None:
            return fast_streq

    if node.name in ("strlen", "len") and len(node.args) == 1:
        return self._emit_known_strlen(arg_at(node, 0))

    if node.name == "char_at" and len(node.args) >= 2:
        from transpiler.expr_string_fastpath import literal_char_at_byte_value

        literal_value = literal_char_at_byte_value(arg_at(node, 0), arg_at(node, 1))
        if literal_value is not None:
            return f"{literal_value}LL"
        if len(node.args) >= 3:
            char_index_value = int_literal_value(arg_at(node, 1))
            length_value = int_literal_value(arg_at(node, 2))
            if (
                char_index_value is not None
                and length_value is not None
                and 0 <= char_index_value < length_value
            ):
                string_expr = self.expr(arg_at(node, 0))
                index_expr = self.expr(arg_at(node, 1))
                self._record_check_decision(
                    node,
                    check_kind="bounds",
                    operation="char_at",
                    decision="elided",
                    reason="literal_length_proven",
                )
                return f"((int64_t)(unsigned char)({string_expr})[{index_expr}])"
        facts = getattr(self, "range_facts", None)
        if (
            facts is not None
            and hasattr(facts, "is_safe_char_at_call")
            and facts.is_safe_char_at_call(self.current_function, node)
        ):
            string_expr = self.expr(arg_at(node, 0))
            index_expr = self.expr(arg_at(node, 1))
            self._record_check_decision(
                node,
                check_kind="bounds",
                operation="char_at",
                decision="elided",
                reason="range_proven",
            )
            return f"((int64_t)(unsigned char)({string_expr})[{index_expr}])"

    call_args = [self.expr(a) for a in node.args]

    declared_type = getattr(self, "_var_types", {}).get(node.name)
    callback_spec = None
    if isinstance(declared_type, str):
        callback_spec = resolve_callback_alias(
            declared_type, getattr(self, "_type_aliases", {})
        )
    if callback_spec is not None:
        params, _ret_type, _decorators = callback_parts(callback_spec)
        if len(call_args) != len(params):
            raise ValueError(
                f"Callback '{node.name}' expects {len(params)} argument(s), "
                f"got {len(call_args)}"
            )
        callee = self._mangle_var(node.name)
        return f"(({declared_type})({callee}))({', '.join(call_args)})"

    # Generic function call: monomorphize and emit if needed
    generic_base = getattr(node, "generic_base", None)
    if generic_base is not None:
        type_args = getattr(node, "generic_type_args", [])
        mangled = self._monomorphizer.instantiate(generic_base, type_args)
        if mangled not in self._generic_funcs_emitted:
            self._generic_funcs_emitted.add(mangled)
            for spec in self._monomorphizer.get_specialized_definitions():
                if isinstance(spec, A.Function) and spec.name == mangled:
                    self.user_defined_funcs.add(mangled)
                    self.visit_Function(spec)
                    break
        return f"{mangled}({', '.join(call_args)})"

    # Handle typeof() - return type name as string at transpile time
    if node.name == "typeof" and node.args:
        type_name = self._infer_typeof(arg_at(node, 0))
        return f'"{type_name}"'

    # Auto-optimize write_file in loops with literal paths
    if node.name == "read_stdin" and not node.args:
        return "read_stdin()"

    if node.name == "write_file" and len(node.args) >= 2:
        path_arg = arg_at(node, 0)
        path_expr, content_expr = call_args[0], call_args[1]
        # If in a loop AND path is a string literal, use streaming version
        if self._loop_depth > 0 and isinstance(path_arg, A.StringLit):
            self._needs_stream_cleanup = True
            return f"write_file_stream({path_expr}, {content_expr})"
        # Otherwise use safe single-write version
        return f"write_file({path_expr}, {content_expr})"

    # Special handling for len() - check if argument is an array
    if node.name == "len" and node.args:
        len_arg = arg_at(node, 0)
        if isinstance(len_arg, A.Variable) and len_arg.name in self._array_vars:
            return f"{len_arg.name}.length"

    # print() used in expression position (rare): keep this total by
    # returning 0 after output, with a typed writer fast path.
    if node.name == "print" and call_args:
        self._record_format_decision(
            node,
            format_kind="print",
            decision="direct_writer",
            reason="expr_single_arg",
        )
        return (
            f"(ailang_write_i64(stdout, (int64_t)({call_args[0]})), "
            "fputc('\\n', stdout), 0)"
        )

    builtins = c_builtin_mappings(self)

    # Only use builtin if user hasn't defined their own
    if node.name in builtins and node.name not in self.user_defined_funcs:
        # `concat(...)` lowers to ailang_concat2/3/4 helpers that
        # take `const char *` and never free their inputs. When ANY
        # arg is itself an owned heap allocation (int_to_str, read_
        # file, user-fn returning string, an inner concat result,
        # etc.) the simple helper leaks that allocation. Route
        # through ailang_strcat_n which already supports per-arg
        # owned flags and frees owned args inside the helper. The
        # non-owning fast path (concat of literals + borrowed vars)
        # still uses concat2/3/4. This was the second half of the
        # io_probe.ail leak Codex surfaced (2026-04-30).
        if (
            node.name == "concat"
            and len(node.args) >= 2
            and any(self._is_owned_string_alloc(a) for a in node.args)
        ):
            self.used_helpers.add("strcat_n")
            return self._emit_strcat_n(list(node.args))
        return builtins[node.name](call_args)

    # Handle SIMD functions with type-aware dispatch (vec32b -> 256, vec64b -> 512)
    if node.name.startswith("vec_") and len(node.args) >= 1:
        vec_type = None

        # Check if last argument is a type string
        last_arg = node.args[-1]
        if isinstance(last_arg, A.StringLit):
            if last_arg.value in ("vec32b", "vec256", "vec4l"):
                vec_type = "256"
            elif last_arg.value in ("vec64b", "vec512", "vec8l"):
                vec_type = "512"

        # Also check if any argument is a known vec256/vec512 variable
        if vec_type is None:
            for arg in node.args:
                if isinstance(arg, A.Variable):
                    func_scope = self.current_function
                    # Check vec256
                    if (
                        func_scope in self._vec256_vars
                        and arg.name in self._vec256_vars[func_scope]
                    ):
                        vec_type = "256"
                        break
                    if (
                        None in self._vec256_vars
                        and arg.name in self._vec256_vars[None]
                    ):
                        vec_type = "256"
                        break
                    # Check vec512
                    if (
                        func_scope in self._vec512_vars
                        and arg.name in self._vec512_vars[func_scope]
                    ):
                        vec_type = "512"
                        break
                    if (
                        None in self._vec512_vars
                        and arg.name in self._vec512_vars[None]
                    ):
                        vec_type = "512"
                        break

        if vec_type:
            # Dispatch to wider SIMD function
            base_name = node.name  # e.g., vec_broadcast
            suffix = vec_type  # e.g., "256"
            func_name = f"{base_name}{suffix}"
            call_args_casted = list(call_args)
            # Raw pointers are modeled as int64_t in AILang surface.
            # SIMD load/store helpers take real C pointers, so cast here.
            if base_name in ("vec_load", "vec_loadu") and call_args_casted:
                call_args_casted[0] = (
                    f"(const void *)(uintptr_t)({call_args_casted[0]})"
                )
            elif base_name in ("vec_store", "vec_storeu") and call_args_casted:
                call_args_casted[0] = f"(void *)(uintptr_t)({call_args_casted[0]})"
            args_str = ", ".join(call_args_casted)
            return f"{func_name}({args_str})"

    # Fill in default arguments if needed
    if node.name in self._func_defaults and node.name in self.functions:
        expected_params = len(self.functions[node.name][0])
        if len(call_args) < expected_params:
            # Fill in missing args with defaults
            defaults = self._func_defaults[node.name]
            for param_idx, default_val in defaults:
                if param_idx >= len(call_args):
                    call_args.append(self.expr(default_val))

    # Use mangled name for function calls
    mangled_name = self._mangle_name(node.name)

    # Cast arguments to expected parameter types to suppress
    # -Wint-conversion and -Woverflow (narrowing) warnings
    if node.name in self.functions:
        param_types = self.functions[node.name][0]
        for i, arg_val in enumerate(call_args):
            if i < len(param_types):
                c_type = self._ailang_type_to_c(param_types[i])
                is_str_arg = arg_val.startswith('"') or (
                    i < len(node.args) and self._might_be_string(node.args[i])
                )
                # String → integer parameter: cast via uintptr_t
                # (AILang represents strings as int64_t pointers)
                if is_str_arg and c_type in (
                    "int64_t",
                    "int32_t",
                    "int16_t",
                    "int8_t",
                ):
                    call_args[i] = f"({c_type})(uintptr_t)({arg_val})"
                # Narrowing cast only for scalar/integral C types.
                # ISO C forbids casting nonscalar to nonscalar (-Wpedantic),
                # so never cast struct/record types (PascalCase or ailang_*).
                elif c_type in (
                    "int32_t",
                    "int16_t",
                    "int8_t",
                    "uint64_t",
                    "uint32_t",
                    "uint16_t",
                    "uint8_t",
                    "float",
                    "double",
                    "long double",
                    "bool",
                    "uintptr_t",
                    "size_t",
                    "ptrdiff_t",
                ):
                    arg_node = node.args[i] if i < len(node.args) else None
                    arg_type = (
                        self._infer_type(arg_node)
                        if arg_node is not None
                        else "int64_t"
                    )
                    if arg_type != c_type:
                        call_args[i] = f"({c_type})({arg_val})"

    args_str = ", ".join(call_args)
    return f"{mangled_name}({args_str})"
