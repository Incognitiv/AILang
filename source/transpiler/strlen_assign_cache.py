"""C-backend strlen cache helpers for string-producing assignments."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Dict, Iterable, Optional

from ast_access import arg_at
from transpiler.expr_string_fastpath import static_string_byte_length

_BASECONV_LEN_HELPERS = {
    "hex": "ailang_hex_len_u64",
    "bin": "ailang_bin_len_u64",
    "oct": "ailang_oct_len_u64",
}


def strlen_cache_var_name(var_name: str) -> str:
    return f"__ailang_strlen_{var_name}"


def _is_integer_type_name(owner: Any, type_name: Any) -> bool:
    if owner._is_integer_type_name(type_name):
        return True
    lowered = str(type_name).strip().lower()
    return lowered in {
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "size_t",
        "ssize_t",
        "long long",
        "unsigned long long",
    }


def _is_known_integer_expr(
    owner: Any, node: A.ASTNode, vars_found: Optional[Dict[str, str]] = None
) -> bool:
    if isinstance(node, A.Number):
        return not node.is_float
    if isinstance(node, A.Variable):
        var_type = None
        if vars_found is not None:
            var_type = vars_found.get(node.name)
        if var_type is None:
            var_type = getattr(owner, "_var_types", {}).get(node.name)
        return var_type is not None and _is_integer_type_name(owner, var_type)
    if isinstance(node, A.UnaryOp):
        return node.op in ("+", "plus", "-", "minus") and _is_known_integer_expr(
            owner, node.operand, vars_found
        )
    if isinstance(node, A.BinaryOp):
        if node.op not in {"+", "plus", "-", "minus", "*", "%", "/", "//", "mod"}:
            return False
        return _is_known_integer_expr(owner, node.left, vars_found) and (
            _is_known_integer_expr(owner, node.right, vars_found)
        )
    return False


def str_known_integer_arg(
    owner: Any, node: A.ASTNode, vars_found: Optional[Dict[str, str]] = None
) -> Optional[A.ASTNode]:
    if not isinstance(node, A.Call) or node.name != "str" or len(node.args) != 1:
        return None
    arg = arg_at(node, 0)
    if _is_known_integer_expr(owner, arg, vars_found):
        return arg
    return None


def baseconv_known_integer_arg(
    owner: Any, node: A.ASTNode, vars_found: Optional[Dict[str, str]] = None
) -> Optional[tuple[str, A.ASTNode]]:
    if (
        not isinstance(node, A.Call)
        or node.name not in _BASECONV_LEN_HELPERS
        or len(node.args) != 1
    ):
        return None
    arg = arg_at(node, 0)
    if _is_known_integer_expr(owner, arg, vars_found):
        return node.name, arg
    return None


def baseconv_len_expr(emitter: Any, kind: str, arg: A.ASTNode) -> str:
    emitter.used_helpers.add("base_conv_len")
    helper = _BASECONV_LEN_HELPERS[kind]
    return f"{helper}((uint64_t)({emitter.expr(arg)}))"


def string_length_producer_arg(
    owner: Any, node: A.ASTNode, vars_found: Optional[Dict[str, str]] = None
) -> Optional[A.ASTNode]:
    str_arg = str_known_integer_arg(owner, node, vars_found)
    if str_arg is not None:
        return str_arg
    base_arg = baseconv_known_integer_arg(owner, node, vars_found)
    if base_arg is not None:
        _kind, arg = base_arg
        return arg
    return None


def _has_cached_strlen(var_name: str, vars_found: Optional[Dict[str, str]]) -> bool:
    if vars_found is None:
        return False
    return strlen_cache_var_name(var_name) in vars_found


def interpolation_known_length(
    owner: Any, node: A.ASTNode, vars_found: Optional[Dict[str, str]] = None
) -> bool:
    if not isinstance(node, A.InterpolatedString):
        return False
    for part in node.parts:
        if isinstance(part, str):
            continue
        if string_length_producer_arg(owner, part, vars_found) is not None:
            continue
        if isinstance(part, A.Variable):
            if _has_cached_strlen(part.name, vars_found):
                continue
            var_type = None if vars_found is None else vars_found.get(part.name)
            if var_type is None:
                var_type = getattr(owner, "_var_types", {}).get(part.name)
            if var_type is not None and _is_integer_type_name(owner, var_type):
                continue
        return False
    return True


def is_length_only_string_producer(
    owner: Any, node: Optional[A.ASTNode], vars_found: Optional[Dict[str, str]] = None
) -> bool:
    if node is None:
        return False
    if string_length_producer_arg(owner, node, vars_found) is not None:
        return True
    return interpolation_known_length(owner, node, vars_found)


def collect_strlen_cache_var(
    owner: Any, var_name: str, value: A.ASTNode, vars_found: Dict[str, str]
) -> None:
    if is_length_only_string_producer(owner, value, vars_found):
        vars_found.setdefault(strlen_cache_var_name(var_name), "int64_t")


def update_strlen_cache_after_assign(
    emitter: Any, var_name: str, value: A.ASTNode
) -> None:
    cache = getattr(emitter, "_c_strlen_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(emitter, "_c_strlen_cache", cache)

    static_len = static_string_byte_length(value)
    if static_len is not None:
        cache[var_name] = f"{static_len}LL"
        return

    cache_var = strlen_cache_var_name(var_name)
    value_arg = str_known_integer_arg(emitter, value)
    if value_arg is not None:
        emitter.used_helpers.add("i64_decimal_len")
        emitter.emit(
            f"{cache_var} = ailang_i64_decimal_len({emitter.expr(value_arg)});"
        )
        cache[var_name] = cache_var
        return

    base_arg = baseconv_known_integer_arg(emitter, value)
    if base_arg is not None:
        kind, arg = base_arg
        emitter.emit(f"{cache_var} = {baseconv_len_expr(emitter, kind, arg)};")
        cache[var_name] = cache_var
        return
    if interpolation_known_length(emitter, value, getattr(emitter, "_var_types", {})):
        emitter.emit(f"{cache_var} = {emitter._emit_known_strlen(value)};")
        cache[var_name] = cache_var
        return

    cache.pop(var_name, None)


def collect_length_only_string_locals(
    owner: Any, body: Iterable[A.ASTNode], vars_found: Dict[str, str]
) -> set[str]:
    candidates: set[str] = set()
    rejected: set[str] = set()
    write_contexts: dict[str, set[int]] = {}
    read_contexts: dict[str, set[int]] = {}
    deferred_assignments: list[tuple[str, A.ASTNode, int]] = []

    def note_write(var_name: str, value: Optional[A.ASTNode], ctx: int) -> None:
        if is_length_only_string_producer(owner, value, vars_found):
            if var_name not in rejected:
                candidates.add(var_name)
                write_contexts.setdefault(var_name, set()).add(ctx)
            return
        candidates.discard(var_name)
        rejected.add(var_name)

    def scan_body(nodes: Iterable[A.ASTNode], reader: Any) -> None:
        scoped = nodes if isinstance(nodes, list) else list(nodes)
        ctx = id(scoped)
        for item in scoped:
            reader(item, ctx)

    def child_bodies(node: Any) -> list[Iterable[A.ASTNode]]:
        out: list[Iterable[A.ASTNode]] = []
        for attr in (
            "body",
            "then_body",
            "else_body",
            "try_body",
            "finally_block",
            "default_case",
        ):
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

    def read_expr(node: Any, ctx: int, len_context: bool = False) -> None:
        if node is None:
            return
        if isinstance(node, A.Variable):
            if node.name in candidates and not len_context:
                candidates.discard(node.name)
                rejected.add(node.name)
            elif node.name in candidates:
                read_contexts.setdefault(node.name, set()).add(ctx)
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
        if isinstance(node, A.Assign):
            read_expr(node.value, ctx, False)
            return
        if isinstance(node, A.VarDecl):
            read_expr(node.init_value, ctx, False)
            return
        for nested in child_bodies(node):
            scan_body(nested, read_node)
        if isinstance(node, A.ASTNode):
            for child in vars(node).values():
                if isinstance(child, list):
                    continue
                read_expr(child, ctx, False)
        elif isinstance(node, (list, tuple)):
            for item in node:
                read_expr(item, ctx, False)

    def collect_writes(node: Any, ctx: int) -> None:
        if node is None:
            return
        if isinstance(node, A.Assign):
            note_write(node.var_name, node.value, ctx)
            return
        if isinstance(node, A.VarDecl):
            note_write(node.var_name, node.init_value, ctx)
            return
        for nested in child_bodies(node):
            scan_body(nested, collect_writes)
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
            if is_length_only_string_producer(owner, node.value, vars_found):
                deferred_assignments.append((node.var_name, node.value, ctx))
                return
        if isinstance(node, A.VarDecl):
            if is_length_only_string_producer(owner, node.init_value, vars_found):
                deferred_assignments.append((node.var_name, node.init_value, ctx))
                return
        read_expr(node, ctx, False)

    top = list(body)
    scan_body(top, collect_writes)
    scan_body(top, read_node)

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


def emit_length_only_string_reassign(
    emitter: Any, var_name: str, value: A.ASTNode
) -> bool:
    if var_name not in (getattr(emitter, "_length_only_string_locals", None) or set()):
        return False
    return is_length_only_string_producer(
        emitter, value, getattr(emitter, "_var_types", {})
    )
