"""Unsafe-operation AST scanning helpers for fast JIT."""

from __future__ import annotations

from parser import ast as A

from runtime.unsafe_registry import register_unsafe


def scan_for_unsafe(nodes: list[A.ASTNode], line_hint: int = 0) -> None:
    """Recursively scan AST for unsafe operations and register them."""
    for node in nodes:
        _scan_node_for_unsafe(node, line_hint)


def _scan_node_for_unsafe(node: A.ASTNode, line_hint: int = 0) -> None:
    """Scan a single AST node for unsafe operations."""
    if node is None:
        return

    if isinstance(node, A.Call):
        if node.unsafe:
            op_type = "function_call"
            if node.name in ("char_at", "unsafe_char_at"):
                op_type = "char_at"
            elif node.name in ("poke", "poke16", "poke32", "poke64"):
                op_type = "poke"
            elif node.name in ("peek", "peek16", "peek32", "peek64"):
                op_type = "peek"
            args_str = ", ".join(["..." for _ in node.args])
            register_unsafe(line_hint, f"{node.name}({args_str}, unsafe)", op_type)
        for arg in node.args:
            _scan_node_for_unsafe(arg, line_hint)
        return

    if isinstance(node, A.ArrayAccess):
        if node.unsafe:
            register_unsafe(line_hint, "array[index, unsafe]", "array_access")
        _scan_node_for_unsafe(node.array, line_hint)
        _scan_node_for_unsafe(node.index, line_hint)
        return

    if isinstance(node, A.Function):
        for stmt in node.body:
            _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.If):
        _scan_node_for_unsafe(node.cond, line_hint)
        for stmt in node.then_body:
            _scan_node_for_unsafe(stmt, line_hint)
        if node.else_body:
            for stmt in node.else_body:
                _scan_node_for_unsafe(stmt, line_hint)
        if hasattr(node, "elsif_branches") and node.elsif_branches:
            for cond, body in node.elsif_branches:
                _scan_node_for_unsafe(cond, line_hint)
                for stmt in body:
                    _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.While):
        _scan_node_for_unsafe(node.cond, line_hint)
        for stmt in node.body:
            _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.For):
        if node.init:
            _scan_node_for_unsafe(node.init, line_hint)
        if node.cond:
            _scan_node_for_unsafe(node.cond, line_hint)
        if node.step:
            _scan_node_for_unsafe(node.step, line_hint)
        for stmt in node.body:
            _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.Foreach):
        _scan_node_for_unsafe(node.iterable, line_hint)
        for stmt in node.body:
            _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.Loop):
        for stmt in node.body:
            _scan_node_for_unsafe(stmt, line_hint)
        return

    if isinstance(node, A.Return):
        if node.value:
            _scan_node_for_unsafe(node.value, line_hint)
        return

    if isinstance(node, A.VarDecl):
        if node.init_value:
            _scan_node_for_unsafe(node.init_value, line_hint)
        return

    if isinstance(node, A.Assign):
        _scan_node_for_unsafe(node.value, line_hint)
        return

    if isinstance(node, A.BinaryOp):
        _scan_node_for_unsafe(node.left, line_hint)
        _scan_node_for_unsafe(node.right, line_hint)
        return

    if isinstance(node, A.UnaryOp):
        _scan_node_for_unsafe(node.operand, line_hint)
        return

    if isinstance(node, A.ClassDef):
        for method in node.methods:
            _scan_node_for_unsafe(method, line_hint)
        return

    if isinstance(node, A.MethodCall):
        _scan_node_for_unsafe(node.object_expr, line_hint)
        for arg in node.args:
            _scan_node_for_unsafe(arg, line_hint)
