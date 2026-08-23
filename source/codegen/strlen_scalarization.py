"""LLVM helpers for length-only string-producing local variables."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Iterable

from ast_access import arg_at
from codegen.strlen_fact_cache import lookup_strlen_fact
from llvmlite import ir
from runtime.modes import CompilationContext


def _is_str_call(node: Any) -> bool:
    return isinstance(node, A.Call) and node.name == "str" and len(node.args) == 1


_BASECONV_CALLS = {"hex", "bin", "oct"}


def _is_baseconv_call(node: Any) -> bool:
    return (
        isinstance(node, A.Call)
        and node.name in _BASECONV_CALLS
        and len(node.args) == 1
    )


def _is_read_file_call(node: Any) -> bool:
    return isinstance(node, A.Call) and node.name == "read_file" and len(node.args) == 1


def _is_length_string_expr(node: Any) -> bool:
    return (
        _is_str_call(node)
        or _is_baseconv_call(node)
        or _is_read_file_call(node)
        or isinstance(node, A.InterpolatedString)
    )


def collect_length_only_str_locals(body: Iterable[A.ASTNode]) -> set[str]:
    candidates: set[str] = set()
    rejected: set[str] = set()
    write_contexts: dict[str, set[int]] = {}
    read_contexts: dict[str, set[int]] = {}
    deferred_assignments: list[tuple[str, Any, int]] = []

    def bodies(node: Any) -> list[Iterable[A.ASTNode]]:
        out: list[Iterable[A.ASTNode]] = []
        for attr in ("body", "then_body", "else_body", "try_body", "finally_block"):
            value = getattr(node, attr, None)
            if value:
                out.append(value)
        for _cond, branch in getattr(node, "elsif_branches", []) or []:
            out.append(branch)
        for item in getattr(node, "cases", []) or []:
            if isinstance(item, tuple) and len(item) >= 2:
                _case_label, case_branch, *_rest = item
                if isinstance(case_branch, list):
                    out.append(case_branch)
        for item in getattr(node, "catch_blocks", []) or []:
            if isinstance(item, tuple) and len(item) >= 3:
                _catch_name, _catch_type, catch_branch, *_rest = item
                if isinstance(catch_branch, list):
                    out.append(catch_branch)
        except_block = getattr(node, "except_block", None)
        if isinstance(except_block, tuple) and len(except_block) >= 2:
            out.append(except_block[1])
        return out

    def scan(nodes: Iterable[A.ASTNode], reader: Any) -> None:
        scoped = nodes if isinstance(nodes, list) else list(nodes)
        ctx = id(scoped)
        for item in scoped:
            reader(item, ctx)

    def note_write(var_name: str, value: Any, ctx: int) -> None:
        if _is_length_string_expr(value):
            if var_name not in rejected:
                candidates.add(var_name)
                write_contexts.setdefault(var_name, set()).add(ctx)
            return
        candidates.discard(var_name)
        rejected.add(var_name)

    def read_expr(node: Any, ctx: int, len_context: bool = False) -> None:
        if node is None:
            return
        if isinstance(node, A.Variable):
            if node.name in candidates and len_context:
                read_contexts.setdefault(node.name, set()).add(ctx)
            elif node.name in candidates:
                candidates.discard(node.name)
                rejected.add(node.name)
            return
        if isinstance(node, A.Call):
            if node.name in {"len", "strlen"} and node.args:
                read_expr(arg_at(node, 0), ctx, True)
                for arg in node.args[1:]:
                    read_expr(arg, ctx, False)
                return
            for arg in node.args:
                read_expr(arg, ctx, False)
            return
        if isinstance(node, A.InterpolatedString):
            for part in node.parts:
                if not isinstance(part, str):
                    read_expr(part, ctx, len_context)
            return
        if isinstance(node, (A.ArrayLit, A.TupleLit)):
            for item in node.elements:
                read_expr(item, ctx, False)
            return
        if isinstance(node, A.DictLit):
            for key, value in node.pairs:
                read_expr(key, ctx, False)
                read_expr(value, ctx, False)
            return
        if isinstance(node, A.ListComprehension):
            read_expr(node.expr, ctx, False)
            read_expr(node.iterable, ctx, False)
            read_expr(node.condition, ctx, False)
            return
        if isinstance(node, A.Range):
            read_expr(node.start, ctx, False)
            read_expr(node.end, ctx, False)
            return
        if isinstance(node, (A.Assign, A.VarDecl)):
            read_expr(getattr(node, "value", None), ctx, False)
            read_expr(getattr(node, "init_value", None), ctx, False)
            return
        for nested in bodies(node):
            scan(nested, read_node)
        if isinstance(node, A.ASTNode):
            for child in vars(node).values():
                if isinstance(child, list):
                    continue
                read_expr(child, ctx, False)
        elif isinstance(node, (list, tuple)):
            for item in node:
                read_expr(item, ctx, False)

    def collect_writes(node: Any, ctx: int) -> None:
        if isinstance(node, A.Assign):
            note_write(node.var_name, node.value, ctx)
            return
        if isinstance(node, A.VarDecl):
            note_write(node.var_name, node.init_value, ctx)
            return
        for nested in bodies(node):
            scan(nested, collect_writes)
        if isinstance(node, A.ASTNode):
            for child in vars(node).values():
                if isinstance(child, list):
                    continue
                collect_writes(child, ctx)
        elif isinstance(node, (list, tuple)):
            for item in node:
                collect_writes(item, ctx)

    def read_node(node: Any, ctx: int) -> None:
        if isinstance(node, A.Assign):
            if node.var_name in candidates and _is_length_string_expr(node.value):
                deferred_assignments.append((node.var_name, node.value, ctx))
                return
            read_expr(node.value, ctx, False)
            return
        if isinstance(node, A.VarDecl):
            if node.var_name in candidates and _is_length_string_expr(node.init_value):
                deferred_assignments.append((node.var_name, node.init_value, ctx))
                return
            read_expr(node.init_value, ctx, False)
            return
        read_expr(node, ctx, False)

    top = list(body)
    scan(top, collect_writes)
    scan(top, read_node)

    def is_len_only_target(var_name: str) -> bool:
        if var_name in rejected or var_name not in candidates:
            return False
        return read_contexts.get(var_name, set()).issubset(
            write_contexts.get(var_name, set())
        )

    changed = True
    while changed:
        before_candidates = set(candidates)
        before_rejected = set(rejected)
        before_reads = {name: set(ctxs) for name, ctxs in read_contexts.items()}
        for var_name, value, ctx in deferred_assignments:
            read_expr(value, ctx, is_len_only_target(var_name))
        changed = (
            before_candidates != candidates
            or before_rejected != rejected
            or before_reads != read_contexts
        )

    return {
        name
        for name in candidates - rejected
        if read_contexts.get(name, set()).issubset(write_contexts.get(name, set()))
    }


def _get_ctlz_i64(cg: Any) -> ir.Function:
    func_name = "llvm.ctlz.i64"
    existing = cg.module.globals.get(func_name)
    if existing is not None:
        return existing
    i64 = ir.IntType(64)
    i1 = ir.IntType(1)
    return ir.Function(cg.module, ir.FunctionType(i64, [i64, i1]), func_name)


def _emit_baseconv_len(cg: Any, kind: str, value: ir.Value) -> ir.Value | None:
    if not isinstance(value.type, ir.IntType):
        return None
    builder = cg.current_builder
    i64 = ir.IntType(64)
    value64 = cg.ensure_int64(value)
    zero = ir.Constant(i64, 0)
    is_zero = builder.icmp_unsigned("==", value64, zero, name=f"{kind}_is_zero")
    ctlz = builder.call(
        _get_ctlz_i64(cg),
        [value64, ir.Constant(ir.IntType(1), 0)],
        name=f"{kind}_ctlz",
    )
    bits = builder.sub(ir.Constant(i64, 64), ctlz, name=f"{kind}_bits")
    if kind == "hex":
        digits = builder.lshr(
            builder.add(bits, ir.Constant(i64, 3)), ir.Constant(i64, 2)
        )
    elif kind == "bin":
        digits = bits
    else:
        digits = builder.udiv(
            builder.add(bits, ir.Constant(i64, 2)), ir.Constant(i64, 3)
        )
    prefixed = builder.add(digits, ir.Constant(i64, 2), name=f"{kind}_prefixed_len")
    return builder.select(is_zero, ir.Constant(i64, 3), prefixed, name=f"{kind}_len")


def _emit_read_file_len(cg: Any, node: Any) -> ir.Value | None:
    if not _is_read_file_call(node):
        return None
    CompilationContext.require_feature("file_io", "read_file() length scalarization")
    builder = cg.current_builder
    int64 = ir.IntType(64)
    int32 = ir.IntType(32)
    filename = cg.generate_expr(arg_at(node, 0))
    file_ptr = builder.call(
        cg.get_fopen(),
        [filename, cg.create_string_constant("rb")],
        name="read_len_fopen",
    )
    null_ptr = ir.Constant(file_ptr.type, None)
    file_ok = builder.icmp_unsigned("!=", file_ptr, null_ptr, name="read_len_ok")
    success_block = cg.current_function.append_basic_block("read_len_success")
    error_block = cg.current_function.append_basic_block("read_len_error")
    merge_block = cg.current_function.append_basic_block("read_len_merge")
    builder.cbranch(file_ok, success_block, error_block)

    builder.position_at_end(error_block)
    builder.branch(merge_block)
    error_end = builder.block

    builder.position_at_end(success_block)
    zero64 = ir.Constant(int64, 0)
    seek_end = ir.Constant(int32, 2)
    seek_set = ir.Constant(int32, 0)
    builder.call(cg.get_fseek(), [file_ptr, zero64, seek_end])
    raw_len = builder.call(cg.get_ftell(), [file_ptr], name="read_len_raw")
    builder.call(cg.get_fseek(), [file_ptr, zero64, seek_set])
    builder.call(cg.get_fclose(), [file_ptr])
    len_is_negative = builder.icmp_signed("<", raw_len, zero64, name="read_len_neg")
    safe_len = builder.select(
        len_is_negative, zero64, raw_len, name="read_len_nonnegative"
    )
    builder.branch(merge_block)
    success_end = builder.block

    builder.position_at_end(merge_block)
    phi = builder.phi(int64, name="read_file_len_scalar")
    phi.add_incoming(zero64, error_end)
    phi.add_incoming(safe_len, success_end)
    return phi


def try_emit_baseconv_strlen(cg: Any, node: A.ASTNode) -> ir.Value | None:
    if not isinstance(node, A.Call) or not _is_baseconv_call(node):
        return None
    value = cg.generate_expr(arg_at(node, 0))
    if not isinstance(value.type, ir.IntType):
        return None
    return _emit_baseconv_len(cg, node.name, value)


def _is_known_integer_expr(cg: Any, node: A.ASTNode) -> bool:
    string_emitter = getattr(cg, "builtin_string", None)
    checker = getattr(string_emitter, "_str_arg_is_known_integer", None)
    if checker is None:
        return isinstance(node, A.Number) and not node.is_float
    return bool(checker(node))


def _literal_strlen(text: str) -> ir.Constant:
    return ir.Constant(ir.IntType(64), len(text.encode("utf-8")))


def _add_lengths(cg: Any, values: list[ir.Value]) -> ir.Value | None:
    if not values:
        return ir.Constant(ir.IntType(64), 0)
    total = cg.ensure_int64(values[0])
    for idx, value in enumerate(values[1:], start=1):
        total = cg.current_builder.add(
            total, cg.ensure_int64(value), name=f"interp_strlen_sum_{idx}"
        )
    return total


def _variable_string_type(cg: Any, var_name: str) -> str | None:
    type_name = getattr(cg, "local_decl_types", {}).get(var_name)
    if type_name is None:
        return None
    lowered = str(type_name).strip().lower()
    if lowered in {"string", "str"}:
        return "string"
    string_emitter = getattr(cg, "builtin_string", None)
    checker = getattr(string_emitter, "_is_integer_type_name", None)
    if checker is not None and checker(type_name):
        return "integer"
    if lowered in {"int", "i64", "u64", "long", "uint", "usize", "isize"}:
        return "integer"
    return None


def _emit_variable_part_strlen(cg: Any, node: A.Variable) -> ir.Value | None:
    cached = lookup_strlen_fact(cg, node)
    if cached is not None:
        return cg.ensure_int64(cached)
    kind = _variable_string_type(cg, node.name)
    if kind == "integer":
        value = cg.generate_expr(node)
        if not isinstance(value.type, ir.IntType):
            return None
        return cg.current_builder.call(
            cg.get_i64_decimal_len_func(),
            [cg.ensure_int64(value)],
            name=f"{node.name}_interp_i64_strlen",
        )
    if kind == "string":
        value = cg.generate_expr(node)
        if not isinstance(value.type, ir.PointerType):
            return None
        return cg.current_builder.call(
            cg.get_strlen(), [value], name=f"{node.name}_interp_strlen"
        )
    return None


def try_emit_known_string_length(cg: Any, value_node: A.ASTNode) -> ir.Value | None:
    """Return the length of a pure string-producing expression without allocating it."""
    if _is_str_call(value_node):
        value_arg = arg_at(value_node, 0)
        if not _is_known_integer_expr(cg, value_arg):
            return None
        value = cg.generate_expr(value_arg)
        if not isinstance(value.type, ir.IntType):
            return None
        return cg.current_builder.call(
            cg.get_i64_decimal_len_func(),
            [cg.ensure_int64(value)],
            name="known_i64_strlen",
        )
    if _is_baseconv_call(value_node):
        return try_emit_baseconv_strlen(cg, value_node)
    if _is_read_file_call(value_node):
        return _emit_read_file_len(cg, value_node)
    if isinstance(value_node, A.StringLit):
        return _literal_strlen(value_node.value)
    if isinstance(value_node, A.InterpolatedString):
        lengths: list[ir.Value] = []
        for part in value_node.parts:
            if isinstance(part, str):
                lengths.append(_literal_strlen(part))
            elif isinstance(part, A.Variable):
                part_len = _emit_variable_part_strlen(cg, part)
                if part_len is None:
                    return None
                lengths.append(part_len)
            elif _is_length_string_expr(part):
                part_len = try_emit_known_string_length(cg, part)
                if part_len is None:
                    return None
                lengths.append(part_len)
            elif _is_known_integer_expr(cg, part):
                value = cg.generate_expr(part)
                if not isinstance(value.type, ir.IntType):
                    return None
                lengths.append(
                    cg.current_builder.call(
                        cg.get_i64_decimal_len_func(),
                        [cg.ensure_int64(value)],
                        name="interp_i64_strlen",
                    )
                )
            else:
                return None
        return _add_lengths(cg, lengths)
    return None


def try_emit_length_only_str_assignment(
    cg: Any, var_name: str, value_node: A.ASTNode
) -> tuple[ir.Value, ir.Value] | None:
    if var_name not in (getattr(cg, "_llvm_length_only_string_locals", None) or set()):
        return None
    length = try_emit_known_string_length(cg, value_node)
    if length is None:
        return None
    placeholder = ir.Constant(ir.IntType(8).as_pointer(), None)
    return placeholder, length
