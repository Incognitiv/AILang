"""C-backend value-semantics helpers for AILang ``unbounded`` integers."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str

from transpiler.fixed_int_types import info_for_fixed_int


def is_unbounded_spec(transpiler, spec: object) -> bool:
    try:
        resolved = transpiler._resolve_type_alias_spec(spec)
    except Exception:
        resolved = spec
    return parsed_type_to_str(resolved).strip().lower() == "unbounded"


def expr_is_unbounded(transpiler, node: A.ASTNode) -> bool:
    if isinstance(node, A.Variable):
        return is_unbounded_spec(transpiler, transpiler._var_types.get(node.name, ""))
    if isinstance(node, A.Call) and node.name in transpiler.functions:
        return is_unbounded_spec(transpiler, transpiler.functions[node.name][1])
    if isinstance(node, A.BinaryOp):
        # BigInt operands do not imply a BigInt *result*. Comparisons lower
        # through bigint_cmp but their language-level value is bool. Treating
        # `x > 0` as an owning BigInt made print/assignment paths reinterpret
        # 0/1 as a pointer (e.g. 0x1) and crash.
        if node.op in ("==", "!=", "<", ">", "<=", ">="):
            return False
        return expr_is_unbounded(transpiler, node.left) or expr_is_unbounded(
            transpiler, node.right
        )
    if isinstance(node, A.UnaryOp):
        return expr_is_unbounded(transpiler, node.operand)
    if isinstance(node, A.FieldAccess):
        cls = transpiler._class_ptr_type(node.object_expr)
        if cls is None and isinstance(node.object_expr, A.ThisExpr):
            cls = transpiler._current_class
        if cls:
            ft = transpiler._field_ailang_type(cls, node.field_name)
            return ft is not None and is_unbounded_spec(transpiler, ft)
    return False


def expression_is_borrowed_bigint(node: A.ASTNode) -> bool:
    return isinstance(node, (A.Variable, A.FieldAccess, A.ThisExpr))


def _source_fixed_info(transpiler, node: A.ASTNode):
    if isinstance(node, A.Variable):
        info = info_for_fixed_int(transpiler._var_types.get(node.name, ""))
        if info is not None:
            return info
    c_type = transpiler._infer_type(node)
    from transpiler.fixed_int_types import info_for_c_fixed

    return info_for_c_fixed(c_type)


def fixed_expr_to_bigint(transpiler, node: A.ASTNode, code: str) -> str:
    info = _source_fixed_info(transpiler, node)
    if info is None:
        # Untyped integer expressions in AILang are signed i64.
        return f"ailang_bigint_from_int((int64_t)({code}))"
    return f"ailang_bigint_from_{info.canonical}_value(({transpiler._ailang_type_to_c(info.canonical)})({code}))"


def owned_bigint_expr(transpiler, node: A.ASTNode, code: str | None = None) -> str:
    """Return C text producing a fresh owning BigInt object."""
    if isinstance(node, A.Number):
        if isinstance(node.value, float):
            raise ValueError("floating-point value cannot initialize unbounded")
        return f'ailang_bigint_from_decimal("{int(node.value)}")'
    if code is None:
        code = transpiler.expr(node)
    if expr_is_unbounded(transpiler, node):
        if expression_is_borrowed_bigint(node):
            return f"ailang_bigint_clone({code})"
        return code
    return fixed_expr_to_bigint(transpiler, node, code)


def bigint_to_fixed_expr(
    transpiler, node: A.ASTNode, code: str, target_spec: object
) -> str:
    info = info_for_fixed_int(parsed_type_to_str(target_spec))
    if info is None:
        raise ValueError(
            f"unbounded conversion target is not a fixed integer: {target_spec}"
        )
    return f"ailang_bigint_to_{info.canonical}_value({code})"
