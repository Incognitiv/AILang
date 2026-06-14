"""LLVM helpers for length-only ``str(int)`` local variables."""

from __future__ import annotations

from parser import ast as A
from typing import Any, Iterable

from ast_access import arg_at
from llvmlite import ir


def _is_str_call(node: Any) -> bool:
    return isinstance(node, A.Call) and node.name == "str" and len(node.args) == 1


def collect_length_only_str_locals(body: Iterable[A.ASTNode]) -> set[str]:
    candidates: set[str] = set()
    rejected: set[str] = set()
    write_contexts: dict[str, set[int]] = {}
    read_contexts: dict[str, set[int]] = {}

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
        if _is_str_call(value):
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
        read_expr(node, ctx, False)

    top = list(body)
    scan(top, collect_writes)
    scan(top, read_node)
    return {
        name
        for name in candidates - rejected
        if read_contexts.get(name, set()).issubset(write_contexts.get(name, set()))
    }


def try_emit_length_only_str_assignment(
    cg: Any, var_name: str, value_node: A.ASTNode
) -> tuple[ir.Value, ir.Value] | None:
    if var_name not in (getattr(cg, "_llvm_length_only_string_locals", None) or set()):
        return None
    if not _is_str_call(value_node):
        return None
    value = cg.generate_expr(arg_at(value_node, 0))
    if not isinstance(value.type, ir.IntType):
        return None
    length = cg.current_builder.call(
        cg.get_i64_decimal_len_func(),
        [cg.ensure_int64(value)],
        name=f"{var_name}_i64_strlen",
    )
    placeholder = ir.Constant(ir.IntType(8).as_pointer(), None)
    return placeholder, length
