"""Basic C expression lowering helpers."""

from __future__ import annotations

from parser import ast as A
from typing import Any, List


def _expr_literal(self: Any, node: A.ASTNode) -> str:
    """Generate C code for literal values."""
    if isinstance(node, A.Number):
        if isinstance(node.value, float):
            return str(node.value)
        val = node.value
        if val > 0x7FFFFFFF:
            return f"0x{val:X}ULL"
        if val < -0x80000000:
            return f"{val}LL"
        return f"{val}LL"
    if isinstance(node, A.Bool):
        return "true" if node.value else "false"
    if isinstance(node, A.Null):
        return "nullptr"
    if isinstance(node, A.StringLit):
        escaped = (
            node.value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return "/* unknown literal */"


def _expr_interpolated_string(self: Any, node: A.InterpolatedString) -> str:
    parts_code: List[str] = []
    parts_owned: List[bool] = []
    for part in node.parts:
        if isinstance(part, str):
            escaped = (
                part.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t")
            )
            parts_code.append(f'"{escaped}"')
            parts_owned.append(False)
        else:
            expr_val = self.expr(part)
            if self._might_be_string(part):
                parts_code.append(expr_val)
                parts_owned.append(self._is_owned_string_alloc(part))
            else:
                parts_code.append(f"ailang_int_to_str({expr_val})")
                parts_owned.append(True)
    if not parts_code:
        self._record_format_decision(
            node,
            format_kind="interpolation",
            decision="direct_writer",
            reason="empty_literal",
        )
        return '""'
    result = parts_code[0]
    result_owned = parts_owned[0]
    for index in range(1, len(parts_code)):
        part = parts_code[index]
        part_owned = parts_owned[index]
        if result_owned or part_owned:
            result = (
                f"ailang_strcat_consuming({result}, {part}, "
                f"{1 if result_owned else 0}, {1 if part_owned else 0})"
            )
        else:
            result = f"ailang_strcat({result}, {part})"
        result_owned = True
    self._record_format_decision(
        node,
        format_kind="interpolation",
        decision="direct_writer",
        reason="concat_runtime",
    )
    return result


def _expr_unary_op(self: Any, node: A.UnaryOp) -> str:
    operand = self.expr(node.operand)
    if node.op == "not":
        return f"(!{operand})"
    if node.op == "NOT":
        return f"(~{operand})"
    return f"({node.op}{operand})"


def _expr_ternary_op(self: Any, node: A.TernaryOp) -> str:
    cond = self.expr(node.cond)
    then_val = self.expr(node.true_expr)
    else_val = self.expr(node.false_expr)
    return f"({cond} ? {then_val} : {else_val})"


def _expr_field_access(self: Any, node: A.FieldAccess) -> str:
    obj = self.expr(node.object_expr)
    if isinstance(node.object_expr, A.Variable):
        var_name = node.object_expr.name
        if var_name in self.enums:
            if var_name in self.data_enums:
                variant_name = node.field_name
                data_variants = self.data_enums[var_name]
                if variant_name not in data_variants:
                    return f"(({var_name}){{ .tag = {var_name}_TAG_{variant_name} }})"
            return f"{var_name}_{node.field_name}"
    if isinstance(node.object_expr, A.ThisExpr):
        inline_this = getattr(self, "_inline_this_expr", None)
        if inline_this is not None:
            return f"{inline_this}->{node.field_name}"
        return f"self->{node.field_name}"
    if self._class_ptr_type(node.object_expr) is not None:
        return f"{obj}->{node.field_name}"
    return f"{obj}.{node.field_name}"


def _expr_tuple_lit(self: Any, node: A.TupleLit) -> str:
    elements = ", ".join(self.expr(e) for e in node.elements)
    fields = ", ".join(f"int64_t _{index}" for index in range(len(node.elements)))
    return f"((struct {{ {fields} }}){{ {elements} }})"


def _expr_string_slice(self: Any, node: A.StringSlice) -> str:
    target = self.expr(node.target)
    start = self.expr(node.start)
    if node.end is not None:
        end = self.expr(node.end)
        return f"ailang_substr({target}, {start}, ({end}) - ({start}))"
    return f"ailang_substr({target}, {start}, -1LL)"


def _expr_comptime(self: Any, node: A.ComptimeExpr) -> str:
    result = self._evaluate_comptime(node.expr)
    if result is not None:
        if isinstance(result, bool):
            return "true" if result else "false"
        if isinstance(result, str):
            escaped = result.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return f"{result}LL" if isinstance(result, int) else str(result)
    return self.expr(node.expr)
