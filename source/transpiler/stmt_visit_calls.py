"""Call/printf/concurrency statement visitors for CStmtEmitter."""

from __future__ import annotations

from parser import ast as A
from typing import List, Optional

from ast_access import arg_at, body_at
from transpiler.class_field_ownership import is_auto_owned_param, is_string_type


def _escape_c_string_fragment(text: str) -> str:
    """Escape a raw string fragment for embedding into a C string literal."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _build_interpolation_writer_plan(
    self, node: A.InterpolatedString
) -> Optional[list[dict[str, object]]]:
    """Build direct-writer chunks for one interpolated-string argument.

    Returns None when the interpolation contains an unsupported shape
    (for example floating formatting), so caller can fall back to the
    general formatter path.
    """
    chunks: list[dict[str, object]] = []
    signed_int_types = {
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "ptrdiff_t",
        "int",
        "long",
        "long long",
    }
    unsigned_int_types = {
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "size_t",
        "uintptr_t",
        "unsigned",
        "unsigned int",
        "unsigned long",
        "unsigned long long",
    }

    for part in node.parts:
        if isinstance(part, str):
            if part:
                chunks.append({"kind": "literal", "text": part})
            continue

        inferred = self._infer_type(part)
        if self._might_be_string(part) or "char" in inferred:
            chunks.append(
                {
                    "kind": "string",
                    "expr": self.expr(part),
                    "owned": bool(self._is_owned_string_alloc(part)),
                }
            )
            continue
        if inferred in ("bool",) or isinstance(part, A.Bool):
            chunks.append({"kind": "bool", "expr": self.expr(part)})
            continue
        if inferred in ("double", "float", "long double"):
            return None
        if inferred in unsigned_int_types:
            chunks.append({"kind": "u64", "expr": self.expr(part)})
            continue
        if inferred in signed_int_types:
            chunks.append({"kind": "i64", "expr": self.expr(part)})
            continue
        return None

    return chunks


def _emit_interpolation_writer_chunks(
    self, chunks: list[dict[str, object]], arg_index: int
) -> None:
    """Emit direct stdout writes for one interpolation plan."""
    owned_tmps: list[str] = []
    for chunk_idx, chunk in enumerate(chunks):
        kind = str(chunk.get("kind", ""))
        if kind == "literal":
            lit = _escape_c_string_fragment(str(chunk.get("text", "")))
            self.emit(f'        ailang_write_str(stdout, "{lit}");')
            continue

        expr = str(chunk.get("expr", "0"))
        if kind == "string":
            if bool(chunk.get("owned", False)):
                tmp = f"__ipr_{arg_index}_{chunk_idx}"
                owned_tmps.append(tmp)
                self.emit(f"        char *{tmp} = (char *)({expr});")
                self.emit(f"        ailang_write_str(stdout, {tmp});")
            else:
                self.emit(f"        ailang_write_str(stdout, {expr});")
            continue
        if kind == "bool":
            self.emit(f"        ailang_write_bool(stdout, (int64_t)({expr}));")
            continue
        if kind == "u64":
            self.emit(f"        ailang_write_u64(stdout, (uint64_t)({expr}));")
            continue
        self.emit(f"        ailang_write_i64(stdout, (int64_t)({expr}));")

    for tmp in owned_tmps:
        self.emit(f"        ailang_safe_free({tmp});")


def _get_printf_spec(self, arg_node: A.ASTNode) -> str:
    """Get printf format specifier for an argument node."""
    if isinstance(arg_node, A.StringLit) or self._might_be_string(arg_node):
        return "%s"
    if isinstance(arg_node, A.Number) and isinstance(arg_node.value, float):
        return "%g"
    if isinstance(arg_node, A.Bool):
        return "%s"
    if isinstance(arg_node, A.Null):
        return "%s"
    if isinstance(arg_node, A.Call):
        if arg_node.name in (
            "str",
            "ailang_strcat",
            "chr",
            "substr",
            "concat",
            "read_stdin",
            "read_file",
            "input",
            "hex",
            "bin",
            "oct",
            "str_replace",
            "typeof",
            "str_array_get",
            "str_array_join",
            "dict_key_at",
            "fn_call_str",
            "split_str_get",
            "dict_get_string",
            "str_array_pop",
            "tcp_recv",
            "win32_full_path",
            "current_dir",
            "list_dir",
            "process_capture",
            "process_capture_argv_env_redirs",
            "process_capture_pipeline_argv_redirs",
            "process_capture_pipeline_argv_env_redirs",
        ):
            return "%s"
        if arg_node.name in ("sqrt", "pow", "float"):
            return "%g"
    if isinstance(arg_node, A.FieldAccess) and self._might_be_string(arg_node):
        return "%s"
    inferred = self._infer_type(arg_node)
    if "char" in inferred:
        return "%s"
    if inferred in ("double", "float"):
        return "%g"
    if inferred == "bool":
        return "%s"
    return "%lld"


def _baseconv_writer_kind(arg_node: A.ASTNode) -> Optional[str]:
    """Return base-conversion writer kind for direct print fast path."""
    if isinstance(arg_node, A.Call) and len(arg_node.args) == 1:
        name = arg_node.name.lower()
        if name in ("hex", "bin", "oct"):
            return name
    return None


def _get_printf_arg(self, spec: str, c_expr: str, arg_node: A.ASTNode) -> str:
    """Wrap a C expression for printf based on format specifier."""
    if spec == "%s":
        if isinstance(arg_node, A.Bool):
            return f'({c_expr} ? "true" : "false")'
        if isinstance(arg_node, A.Null):
            return '"null"'
        return c_expr
    if spec == "%g":
        return c_expr
    return f"(long long)({c_expr})"


def _emit_print_call(self, node: A.Call) -> None:
    """Emit print for multi-argument calls.

    P11 fast path: known-shape `%s`/`%lld` prints route through typed
    writer helpers (`ailang_write_str`/`ailang_write_i64`) to avoid
    generic printf format parsing. Fallback to printf remains for
    float/unknown shapes.

    Owned-string args (interpolated strings, strcat results,
    str()/chr()/hex()/etc.) are captured into temps and freed after
    output so we don't leak temporary buffers at every print.
    """
    if not node.args:
        self.emit("#ifndef AILANG_FREESTANDING")
        self.emit("    fputc('\\n', stdout);")
        self.emit("#endif")
        self._record_format_decision(
            node,
            format_kind="print",
            decision="direct_writer",
            reason="empty_newline",
        )
        return

    interpolation_plans: list[Optional[list[dict[str, object]]]] = []
    baseconv_kinds: list[Optional[str]] = []
    interpolation_direct_supported = True
    for arg in node.args:
        if isinstance(arg, A.InterpolatedString):
            plan = _build_interpolation_writer_plan(self, arg)
            interpolation_plans.append(plan)
            baseconv_kinds.append(None)
            if plan is None:
                interpolation_direct_supported = False
        else:
            interpolation_plans.append(None)
            baseconv_kinds.append(_baseconv_writer_kind(arg))

    expr_args: List[str] = []
    specs: List[str] = []
    for i, arg in enumerate(node.args):
        plan = interpolation_plans[i]
        if plan is not None:
            expr_args.append("")
            specs.append("%s")
        else:
            expr_args.append(self.expr(arg))
            specs.append(self._get_printf_spec(arg))
    owned_flags = [
        spec == "%s" and self._is_owned_string_alloc(arg)
        for spec, arg in zip(specs, node.args, strict=False)
    ]
    fmt_parts: List[str] = []
    c_args: List[str] = []
    temp_names: List[str] = []
    for i, (spec, c_expr, arg_node) in enumerate(
        zip(specs, expr_args, node.args, strict=False)
    ):
        if i > 0:
            fmt_parts.append(" ")
        fmt_parts.append(spec)
        if owned_flags[i]:
            tmp = f"__pr_{i}"
            temp_names.append(tmp)
            c_args.append(self._get_printf_arg(spec, tmp, arg_node))
        else:
            temp_names.append("")
            c_args.append(self._get_printf_arg(spec, c_expr, arg_node))

    fmt_str = "".join(fmt_parts) + "\\n"
    args_str = ", ".join(c_args)
    direct_supported = all(spec in ("%s", "%lld") for spec in specs)
    self.emit("#ifndef AILANG_FREESTANDING")
    if direct_supported and interpolation_direct_supported:
        self.emit("    {")
        for i, owned in enumerate(owned_flags):
            if owned and interpolation_plans[i] is None and baseconv_kinds[i] is None:
                self.emit(f"        char *{temp_names[i]} = (char *){expr_args[i]};")
        for i, spec in enumerate(specs):
            if i > 0:
                self.emit("        fputc(' ', stdout);")
            plan = interpolation_plans[i]
            if plan is not None:
                _emit_interpolation_writer_chunks(self, plan, i)
                continue
            base_kind = baseconv_kinds[i]
            arg_node = node.args[i]
            if base_kind is not None and isinstance(arg_node, A.Call):
                inner_expr = self.expr(arg_at(arg_node, 0))
                if base_kind == "hex":
                    self.emit(
                        f"        ailang_write_hex_u64(stdout, (uint64_t)({inner_expr}));"
                    )
                elif base_kind == "bin":
                    self.emit(
                        f"        ailang_write_bin_u64(stdout, (uint64_t)({inner_expr}));"
                    )
                else:
                    self.emit(
                        f"        ailang_write_oct_u64(stdout, (uint64_t)({inner_expr}));"
                    )
                continue
            if spec == "%s":
                self.emit(f"        ailang_write_str(stdout, {c_args[i]});")
            else:
                self.emit(
                    f"        ailang_write_i64(stdout, (int64_t)({expr_args[i]}));"
                )
        self.emit("        fputc('\\n', stdout);")
        for i, owned in enumerate(owned_flags):
            if owned and interpolation_plans[i] is None and baseconv_kinds[i] is None:
                self.emit(f"        ailang_safe_free({temp_names[i]});")
        self.emit("    }")
        if any(plan is not None for plan in interpolation_plans):
            self._record_format_decision(
                node,
                format_kind="interpolation",
                decision="direct_writer",
                reason="literal_typed_segments",
            )
        self._record_format_decision(
            node,
            format_kind="print",
            decision="direct_writer",
            reason="known_shape",
        )
    else:
        if any(owned_flags):
            self.emit("    {")
            for i, owned in enumerate(owned_flags):
                if owned:
                    self.emit(
                        f"        char *{temp_names[i]} = (char *){expr_args[i]};"
                    )
            self.emit(f'        printf("{fmt_str}", {args_str});')
            for i, owned in enumerate(owned_flags):
                if owned:
                    self.emit(f"        ailang_safe_free({temp_names[i]});")
            self.emit("    }")
        else:
            self.emit(f'    printf("{fmt_str}", {args_str});')
        if not interpolation_direct_supported:
            self._record_format_decision(
                node,
                format_kind="interpolation",
                decision="format_fallback",
                reason="unknown_shape",
                fallback_func="printf",
            )
        fallback_reason = (
            "float_format" if any(spec == "%g" for spec in specs) else "unknown_shape"
        )
        self._record_format_decision(
            node,
            format_kind="print",
            decision="format_fallback",
            reason=fallback_reason,
            fallback_func="printf",
        )
    self.emit("#else")
    self.emit("    {")
    for expr in expr_args:
        if expr:
            self.emit(f"        (void)({expr});")
    self.emit("    }")
    self.emit("#endif")


def _emit_dealloc_arg(self, arg: A.ASTNode) -> None:
    """Emit one explicit dealloc/free target."""
    if isinstance(arg, A.FieldAccess):
        field = arg
        owner_class = None
        if isinstance(field.object_expr, A.ThisExpr):
            owner_class = self._current_class
        elif self._class_ptr_type(field.object_expr) is not None:
            owner_class = self._class_ptr_type(field.object_expr)
        if (
            owner_class is not None
            and field.field_name in self._auto_owned_field_names(owner_class)
        ):
            owner_expr = (
                "self"
                if isinstance(field.object_expr, A.ThisExpr)
                else self.expr(field.object_expr)
            )
            flag = self._class_field_owned_flag(field.field_name)
            field_type = self._field_ailang_type(owner_class, field.field_name)
            kind = self._auto_owned_field_kind(field_type)
            cleanup_lines = (
                self._owned_value_cleanup_lines(
                    kind,
                    field_type,
                    f"__field_owner->{field.field_name}",
                )
                if kind is not None
                else []
            )
            self.emit(f"{{ typeof({owner_expr}) __field_owner = {owner_expr};")
            self.emit(f"  if (__field_owner->{flag}) {{")
            for line in cleanup_lines:
                self.emit(f"    {line}")
            self.emit(f"    __field_owner->{flag} = 0;")
            self.emit("  }")
            self.emit("}")
            return
    if isinstance(arg, A.Variable):
        var_name = arg.name
        param_entry = (getattr(self, "_owned_param_flags", None) or {}).get(var_name)
        if param_entry is not None:
            param_flag, kind, param_type = param_entry
            arg_expr = self.expr(arg)
            self.emit(f"if ({param_flag}) {{")
            for line in self._owned_value_cleanup_lines(kind, param_type, arg_expr):
                self.emit(f"    {line}")
            self.emit(f"    {param_flag} = 0;")
            self.emit("}")
            if kind in {"string", "class", "dict"}:
                self.emit(f"{self._mangle_var(var_name)} = 0;  /* null after free */")
            return
    # Null guard + tracked free + null-after-free. Routing through
    # ailang_safe_free keeps the live-allocation counter accurate.
    arg_expr = self.expr(arg)
    self.emit(f"if (({arg_expr}) != 0) {{")
    self.emit(f"    ailang_safe_free((void *)(uintptr_t)({arg_expr}));")
    self.emit("}")
    if isinstance(arg, A.Variable):
        var_name = arg.name
        self.emit(f"{self._mangle_var(var_name)} = 0;  /* null after free */")
        if var_name in self._mixed_ownership_string_locals:
            self.emit(f"{self._mixed_owned_flag(var_name)} = 0;")
        param_entry = (getattr(self, "_owned_param_flags", None) or {}).get(var_name)
        if param_entry is not None:
            self.emit(f"{param_entry[0]} = 0;")


def visit_Call(self, node: A.Call) -> None:
    """Visit Call as a statement."""
    if node.name == "print":
        self._emit_print_call(node)
    elif node.name in ("dealloc", "free"):
        if not node.args:
            raise ValueError(f"{node.name}() expects at least 1 argument")
        for arg in node.args:
            self._emit_dealloc_arg(arg)
    else:
        # Use _generate_call to handle default arguments
        call_expr = self._generate_call(node)
        self.emit(f"(void)({call_expr});")


def _resolve_method_class(self, node: A.MethodCall) -> Optional[str]:
    """Pick the class that owns `node.method_name` for this call site."""
    cls = self._class_ptr_type(node.object_expr)
    if cls is not None:
        return cls
    owners = [
        cn
        for cn, (_f, methods) in self.classes.items()
        if any(m.name == node.method_name for m in methods)
    ]
    return owners[0] if len(owners) == 1 else None


def _emit_method_call_text(self, node: A.MethodCall) -> str:
    """Render a `obj.method(args)` call as a C expression."""
    cls = self._resolve_method_class(node)
    if cls is None:
        return f"/* MethodCall: unresolved .{node.method_name}() */"
    method = next(
        (
            candidate
            for candidate in self.classes.get(cls, ([], []))[1]
            if candidate.name == node.method_name
        ),
        None,
    )
    inline = _try_inline_stack_method_return_expr(self, node, cls, method)
    if inline is not None:
        return inline
    args = []
    params = method.params if method is not None else []
    for index, arg in enumerate(node.args):
        receiver_stack_owned = isinstance(
            node.object_expr, A.Variable
        ) and node.object_expr.name in (
            getattr(self, "_stack_owned_class_locals", {}) or {}
        )
        can_elide_virtual = receiver_stack_owned and self._can_elide_virtual_string_arg(
            cls, node.method_name, index, arg
        )
        arg_expr = "NULL" if can_elide_virtual else self.expr(arg)
        args.append(arg_expr)
        if index < len(params):
            param = params[index]
            if (
                isinstance(param, tuple)
                and len(param) >= 2
                and is_string_type(param[1])
            ):
                args.append(self._emit_known_strlen(arg, arg_expr))
        if index < len(params) and is_auto_owned_param(params[index], self.classes):
            kind = self._auto_owned_field_kind(param[1]) if len(param) >= 2 else None
            if kind is not None:
                owned = (
                    False
                    if can_elide_virtual
                    else self._expr_produces_owned_value(arg, kind, param[1])
                )
                args.append("1" if owned else "0")
    receiver_is_ptr = self._class_ptr_type(node.object_expr) is not None
    obj = self.expr(node.object_expr)
    receiver = obj if receiver_is_ptr else f"&{obj}"
    all_args = ", ".join([receiver, *args])
    return f"{cls}_{node.method_name}({all_args})"


def _try_inline_stack_method_return_expr(
    self,
    node: A.MethodCall,
    cls: str,
    method: Optional[A.Function],
) -> Optional[str]:
    """Inline trivial stack-local method calls as expressions.

    This is deliberately narrow: only no-arg methods with a single return
    expression on a stack-owned local receiver. It exposes `this.field` to the
    existing field-length and stack-array scalar fast paths without changing
    general method semantics or duplicating multi-statement side effects.
    """
    if method is None or method.name == "init" or method.params or node.args:
        return None
    if len(method.body) != 1 or not isinstance(body_at(method, 0), A.Return):
        return None
    ret_expr = body_at(method, 0).value
    if ret_expr is None:
        return None
    if not isinstance(node.object_expr, A.Variable):
        return None
    receiver_name = node.object_expr.name
    stack_locals = getattr(self, "_stack_owned_class_locals", {}) or {}
    if stack_locals.get(receiver_name) != cls:
        return None

    receiver = self.expr(node.object_expr)
    saved_this_expr = getattr(self, "_inline_this_expr", None)
    saved_this_stack_var = getattr(self, "_inline_this_stack_var", None)
    saved_current_class = getattr(self, "_current_class", None)
    try:
        self._inline_this_expr = receiver
        self._inline_this_stack_var = receiver_name
        self._current_class = cls
        self._record_optimizer_decision(
            node,
            opt_kind="method_inline",
            target=f"{cls}.{node.method_name}",
            decision="inlined",
            reason="single_return_stack_receiver",
            details={"receiver": receiver_name},
        )
        return self.expr(ret_expr)
    finally:
        self._inline_this_expr = saved_this_expr
        self._inline_this_stack_var = saved_this_stack_var
        self._current_class = saved_current_class


def visit_MethodCall(self, node: A.MethodCall) -> None:
    """Visit MethodCall as a statement (e.g., obj.method())."""
    self.emit(f"{self._emit_method_call_text(node)};")


# Threading + channel ops in statement context. Without these,
# `join(h)` / `chan_send(ch, x)` as bare statements fall through
# to the generic "Unhandled AST node" dispatch and silently
# become no-ops — `join` not waiting is especially insidious
# because main races past spawned workers, often before they
# even start, so shared-state updates are silently lost.
def visit_Spawn(self, node: A.Spawn) -> None:
    # Bare-statement spawn discards the handle (immediate detach).
    # Emit (void) cast so the result isn't accidentally treated as
    # a value.
    self.emit(f"(void){self.expr(node)};")


def visit_Join(self, node: A.Join) -> None:
    # Bare `join(h)` waits but discards the result.
    self.emit(f"(void){self.expr(node)};")


def visit_AtomicOp(self, node: A.AtomicOp) -> None:
    self.emit(f"(void){self._expr_atomic(node)};")


def visit_ChannelSend(self, node: A.ChannelSend) -> None:
    self.emit(f"{self._expr_channel(node)};")


def visit_ChannelClose(self, node: A.ChannelClose) -> None:
    self.emit(f"{self._expr_channel(node)};")


def visit_ChannelTrySend(self, node: A.ChannelTrySend) -> None:
    self.emit(f"(void){self._expr_channel(node)};")


def visit_ChannelTryRecv(self, node: A.ChannelTryRecv) -> None:
    self.emit(f"(void){self._expr_channel(node)};")


def visit_ChannelRecv(self, node: A.ChannelRecv) -> None:
    # Recv-as-statement: discard the received value.
    self.emit(f"(void){self._expr_channel(node)};")


def visit_ChannelCreate(self, node: A.ChannelCreate) -> None:
    # Bare-statement create is degenerate (immediate leak), but
    # emit it cleanly anyway so the codegen is total.
    self.emit(f"(void){self._expr_channel(node)};")
