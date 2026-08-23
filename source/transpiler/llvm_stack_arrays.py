"""Conservative LLVM stack backing for local array literals."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any

from llvmlite import ir


def emit(stmtgen: Any, node: A.VarDecl, llvm_type: ir.Type) -> bool:
    """Lower a non-escaping local array literal to stack storage.

    Dynamic arrays normally need a heap header for push/pop/capacity. This
    path is intentionally narrower: direct element reads and local slice/view
    aliases only. Anything that may need the dynamic header stays heap-backed.
    """
    if not isinstance(node.init_value, A.ArrayLit):
        return False
    if not isinstance(llvm_type, ir.PointerType):
        return False
    return _emit_literal(stmtgen, node, node.var_name, node.init_value, llvm_type)


def emit_assign(stmtgen: Any, node: A.Assign) -> bool:
    if (
        node.var_name in stmtgen.codegen.locals
        or node.var_name in stmtgen.codegen.globals
    ):
        return False
    if not isinstance(node.value, A.ArrayLit) or not node.value.elements:
        return False
    values = [stmtgen.codegen.generate_expr(elem) for elem in node.value.elements]
    elem_type = values[0].type
    if any(value.type != elem_type for value in values):
        return False
    return _emit_literal(
        stmtgen,
        node,
        node.var_name,
        node.value,
        elem_type.as_pointer(),
        values=values,
        canon_type="array",
    )


def _emit_literal(
    stmtgen: Any,
    original: A.ASTNode,
    var_name: str,
    literal: A.ArrayLit,
    llvm_type: ir.Type,
    *,
    values: list[ir.Value] | None = None,
    canon_type: str | None = None,
) -> bool:
    if not literal.elements:
        return False
    body = getattr(stmtgen.codegen, "_current_function_body", []) or []
    aliases = _collect_direct_aliases(body, var_name)
    names = {var_name, *aliases}
    metadata_names = {var_name}
    if not _uses_are_stack_safe(body, original, names, metadata_names):
        return False

    if values is None:
        values = [stmtgen.codegen.generate_expr(elem) for elem in literal.elements]
    elem_type = values[0].type
    if any(value.type != elem_type for value in values):
        return False
    array_type = ir.ArrayType(elem_type, len(values))
    storage = stmtgen.codegen.alloca_in_entry_block(
        array_type, f"{var_name}_stack_array"
    )
    for idx, value in enumerate(values):
        slot = stmtgen.builder.gep(
            storage,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
            name=f"{var_name}_stack_elem_{idx}",
        )
        stmtgen.builder.store(value, slot)
    data_ptr = stmtgen.builder.gep(
        storage,
        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
        name=f"{var_name}_stack_ptr",
    )
    if data_ptr.type != llvm_type:
        data_ptr = stmtgen.builder.bitcast(data_ptr, llvm_type, name="stack_arr_cast")
    var_ptr = stmtgen.codegen.alloca_in_entry_block(llvm_type, var_name)
    stmtgen.builder.store(data_ptr, var_ptr)
    stmtgen.codegen.locals[var_name] = var_ptr
    if canon_type is not None:
        canon = canon_type
    elif isinstance(original, A.VarDecl):
        canon = parsed_type_to_str(original.type_name)
    else:
        return False
    stmtgen.codegen.local_decl_types[var_name] = canon
    stmtgen.codegen.var_signedness[var_name] = not canon.startswith("u")
    stmtgen.codegen.set_signedness(var_ptr, stmtgen.codegen.var_signedness[var_name])
    stmtgen.codegen.array_metadata[var_name] = (len(values), elem_type)
    return True


def _collect_direct_aliases(body: list[A.ASTNode], root: str) -> set[str]:
    names = {root}
    changed = True
    while changed:
        changed = False
        for stmt in body:
            if not isinstance(stmt, A.VarDecl) or not isinstance(
                stmt.init_value, A.Variable
            ):
                continue
            if stmt.init_value.name in names and stmt.var_name not in names:
                names.add(stmt.var_name)
                changed = True
    return names - {root}


def _uses_are_stack_safe(
    body: list[A.ASTNode],
    original: A.ASTNode,
    names: set[str],
    metadata_names: set[str],
) -> bool:
    return all(_stmt_ok(stmt, original, names, metadata_names) for stmt in body)


def _stmt_ok(
    node: A.ASTNode,
    original: A.ASTNode,
    names: set[str],
    metadata_names: set[str],
) -> bool:
    if node is original:
        return True
    if isinstance(node, A.VarDecl):
        if node.var_name in names:
            return _is_alias_decl(node, names)
        if _is_alias_decl(node, names):
            return True
        return node.init_value is None or _expr_ok(
            node.init_value, names, metadata_names
        )
    if isinstance(node, A.Assign):
        return node.var_name not in names and _expr_ok(
            node.value, names, metadata_names
        )
    if isinstance(node, A.Return):
        return node.value is None or _expr_ok(node.value, names, metadata_names)
    if isinstance(node, A.FieldAssign):
        return _expr_ok(node.object_expr, names, metadata_names) and _expr_ok(
            node.value, names, metadata_names
        )
    if isinstance(node, A.Call):
        return _expr_ok(node, names, metadata_names)
    for value in vars(node).values():
        if isinstance(value, A.ASTNode) and not _node_ok(
            value, original, names, metadata_names
        ):
            return False
        if isinstance(value, list):
            for item in value:
                if isinstance(item, A.ASTNode) and not _node_ok(
                    item, original, names, metadata_names
                ):
                    return False
    return True


def _node_ok(
    node: A.ASTNode,
    original: A.ASTNode,
    names: set[str],
    metadata_names: set[str],
) -> bool:
    if _is_statement(node):
        return _stmt_ok(node, original, names, metadata_names)
    return _expr_ok(node, names, metadata_names)


def _is_statement(node: A.ASTNode) -> bool:
    return isinstance(
        node,
        (
            A.Assign,
            A.BlockCall,
            A.Break,
            A.Call,
            A.Continue,
            A.FieldAssign,
            A.Return,
            A.VarDecl,
        ),
    )


def _is_alias_decl(node: A.VarDecl, names: set[str]) -> bool:
    return isinstance(node.init_value, A.Variable) and node.init_value.name in names


def _expr_ok(node: A.ASTNode, names: set[str], metadata_names: set[str]) -> bool:
    if isinstance(node, A.Variable):
        return node.name not in names
    if isinstance(node, A.ArrayAccess):
        if isinstance(node.array, A.Variable) and node.array.name in names:
            return _expr_ok(node.index, names, metadata_names)
    if isinstance(node, A.Call):
        if _is_allowed_metadata_call(node, metadata_names):
            return True
    for value in vars(node).values():
        if isinstance(value, A.ASTNode) and not _expr_ok(value, names, metadata_names):
            return False
        if isinstance(value, list):
            for item in value:
                if isinstance(item, A.ASTNode) and not _expr_ok(
                    item, names, metadata_names
                ):
                    return False
    return True


def _is_allowed_metadata_call(node: A.Call, names: set[str]) -> bool:
    args = node.args or []
    if node.name != "array_len" or len(args) != 1:
        return False
    arg = next(iter(args))
    return isinstance(arg, A.Variable) and arg.name in names
