"""C-expression fast paths for fixed-shape string comparisons."""

from __future__ import annotations

from parser import ast as A

from ast_access import arg_at


def _literal_bytes(value: str) -> bytes | None:
    """Return UTF-8 bytes when C-string comparison semantics are unambiguous."""
    if "\0" in value:
        return None
    return value.encode("utf-8")


def _is_stable_expr(node: A.ASTNode) -> bool:
    """True when rendering the expression more than once is side-effect free."""
    if isinstance(node, (A.Number, A.Bool, A.Null, A.StringLit, A.Variable)):
        return True
    if isinstance(node, A.UnaryOp):
        return _is_stable_expr(node.operand)
    if isinstance(node, A.BinaryOp):
        return _is_stable_expr(node.left) and _is_stable_expr(node.right)
    return False


def _c_string_literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _emit_streq_literal_call(self, value_node: A.ASTNode, literal: str) -> str | None:
    literal_raw = _literal_bytes(literal)
    if literal_raw is None:
        return None
    literal_expr = _c_string_literal(literal)
    literal_len = len(literal_raw)
    if (
        isinstance(value_node, A.Call)
        and value_node.name == "substr"
        and len(value_node.args) >= 3
        and all(_is_stable_expr(arg) for arg in value_node.args[:3])
    ):
        source = self.expr(arg_at(value_node, 0))
        start = self.expr(arg_at(value_node, 1))
        length = self.expr(arg_at(value_node, 2))
        return (
            f"ailang_streq_slice_lit({source}, {start}, {length}, "
            f"{literal_expr}, {literal_len}LL)"
        )
    if not _is_stable_expr(value_node):
        return None
    return f"ailang_streq_lit({self.expr(value_node)}, {literal_expr}, {literal_len}LL)"


def emit_streq_literal_fastpath(self, node: A.Call) -> str | None:
    """Lower streq(x, "literal") without libc strcmp on the C backend."""
    if node.name != "streq" or len(node.args) != 2:
        return None
    left, right = node.args
    if isinstance(right, A.StringLit):
        return _emit_streq_literal_call(self, left, right.value)
    if isinstance(left, A.StringLit):
        return _emit_streq_literal_call(self, right, left.value)
    return None


def _int_literal(node: A.ASTNode) -> int | None:
    if not isinstance(node, A.Number):
        return None
    if bool(getattr(node, "is_float", False)):
        return None
    try:
        return int(node.value)
    except (TypeError, ValueError, OverflowError):
        return None


def literal_ord_byte_value(node: A.ASTNode) -> int | None:
    """Return ord("x") for single-byte UTF-8 string literals."""
    if not isinstance(node, A.StringLit):
        return None
    raw = _literal_bytes(node.value)
    if raw is None or len(raw) != 1:
        return None
    return raw[0]


def literal_char_at_byte_value(
    text_node: A.ASTNode, index_node: A.ASTNode
) -> int | None:
    """Return char_at("text", literal_index) as a byte proof when static."""
    if not isinstance(text_node, A.StringLit):
        return None
    index = _int_literal(index_node)
    raw = _literal_bytes(text_node.value)
    if raw is None or index is None:
        return None
    if index < 0 or index >= len(raw):
        return None
    return raw[index]


def static_string_byte_length(node: A.ASTNode) -> int | None:
    """Return known UTF-8 byte length for compile-time string expressions."""
    if isinstance(node, A.StringLit):
        if "\0" in node.value:
            return None
        return len(node.value.encode("utf-8"))
    return None
