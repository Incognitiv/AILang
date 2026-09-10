"""Post-argument special dispatch for C call lowering."""

from __future__ import annotations

from parser import ast as A
from typing import Any

from ast_access import arg_at
from callback_types import callback_parts, resolve_callback_alias
from runtime.modes import CompilationContext
from transpiler.c_bigint import expr_is_unbounded, owned_bigint_expr
from transpiler.expr_gen_call_builtin_map import c_builtin_mappings
from transpiler.fixed_int_types import info_for_c_fixed


def dispatch_callable_io(
    self: Any,
    node: A.Call,
    call_args: list[str],
) -> str | None:
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
    return None


def dispatch_scalar_formatting(
    self: Any,
    node: A.Call,
    call_args: list[str],
) -> str | None:
    # ord() accepts either a string (ASCII code of its first byte) or an
    # already-materialized character code. LLVM has always treated integer
    # arguments as identity; mirror that contract in the C backend instead of
    # blindly indexing every emitted argument with [0].
    if node.name == "ord" and len(node.args) == 1:
        arg_node = arg_at(node, 0)
        arg_expr = call_args[0]
        arg_type = self._infer_type(arg_node)
        if arg_type not in ("const char *", "char *"):
            return f"((int64_t)({arg_expr}))"

    # `str(unbounded)` is implemented by the pure-AIL BigInt module. Route the
    # expression through an owning value so temporaries are consumed and borrowed
    # variables are cloned rather than freed. Never reinterpret the BigInt pointer
    # as an i64 for the generic integer formatter.
    if node.name == "str" and len(node.args) == 1:
        arg_node = arg_at(node, 0)
        if expr_is_unbounded(self, arg_node):
            return f"ailang_bigint_to_decimal_take({owned_bigint_expr(self, arg_node, call_args[0])})"

    # Fixed-width integers wider than 64 bits need matching-width materialized
    # formatting. The generic helpers are i64-only and would silently truncate.
    if node.name in {"str", "hex", "bin", "oct"} and len(node.args) == 1:
        arg_node = arg_at(node, 0)
        arg_expr = call_args[0]
        fixed = info_for_c_fixed(self._infer_type(arg_node))
        if fixed is not None and fixed.bits > 64:
            return f"ailang_{node.name}_{fixed.canonical}({arg_expr})"

    # Special handling for len() - check if argument is an array
    if node.name == "len" and node.args:
        len_arg = arg_at(node, 0)
        if isinstance(len_arg, A.Variable) and len_arg.name in self._array_vars:
            return f"{len_arg.name}.length"

    # print() used in expression position (rare): keep this total by
    # returning 0 after output, with a typed writer fast path.
    if node.name == "print" and call_args:
        if CompilationContext.is_freestanding() and not CompilationContext.is_jit():
            self._record_format_decision(
                node,
                format_kind="print",
                decision="freestanding_noop",
                reason="no_hosted_stdout",
            )
            return "0"
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
    return None


def dispatch_builtin_and_simd(
    self: Any,
    node: A.Call,
    call_args: list[str],
) -> str | None:
    builtins = c_builtin_mappings(self)

    # Dispatch deallocation by the resolved collection element type.
    # dealloc_str_array() is used for two C-side representations.
    # split() returns StringArray, which owns its copied token strings;
    # str_array_new()/str_array_push() return ailang_str_array, whose
    # elements are borrowed. Choose the destructor from tracked type.
    if (
        node.name == "dealloc_str_array"
        and node.name not in self.user_defined_funcs
        and node.args
    ):
        source_arg = arg_at(node, 0)
        if isinstance(source_arg, A.Variable):
            source_type = getattr(self, "_current_local_c_types", {}).get(
                source_arg.name, self._var_types.get(source_arg.name)
            )
            if (
                isinstance(source_type, str)
                and source_type.strip().lower() == "stringarray"
            ):
                return f"ailang_str_array_free(&{call_args[0]})"
        return f"ailang_str_array_free_v2(&{call_args[0]})"

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
    return None
