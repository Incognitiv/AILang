"""Performance-pattern checks for static analysis."""

from __future__ import annotations

from parser import ast as A
from typing import Protocol

from diagnostics.static_analysis_models import AnalysisWarning

_LOOP_NODE_TYPES = (A.While, A.DoWhile, A.For, A.Foreach, A.Loop, A.Repeat)

_STRING_RETURNING_CALLS: set[str] = {
    "str",
    "chr",
    "substr",
    "concat",
    "read_stdin",
    "read_file",
    "input",
    "current_dir",
    "list_dir",
    "process_capture",
    "hex",
    "bin",
    "oct",
    "str_replace",
    "typeof",
    "target_os",
    "target_backend",
    "str_array_get",
    "str_array_join",
    "fn_call_str",
    "split_str_get",
    "dict_get_string",
    "str_array_pop",
    "tcp_recv",
}


class _WarnCollector(Protocol):
    warnings: list[AnalysisWarning]


def _self_concat_other(binop: A.BinaryOp, var_name: str) -> A.ASTNode | None:
    left, right = binop.left, binop.right
    if isinstance(left, A.Variable) and left.name == var_name:
        return right
    if isinstance(right, A.Variable) and right.name == var_name:
        return left
    return None


def _looks_like_string(node: A.ASTNode) -> bool:
    if isinstance(node, A.StringLit):
        return True
    if isinstance(node, A.InterpolatedString):
        return True
    if isinstance(node, A.Call):
        return node.name in _STRING_RETURNING_CALLS
    if isinstance(node, A.BinaryOp) and node.op == "+":
        return _looks_like_string(node.left) or _looks_like_string(node.right)
    return False


def check_string_concat_loops(
    analyzer: _WarnCollector, node: A.ASTNode, in_loop: bool
) -> None:
    """Walk the AST and flag `s = s + EXPR` patterns inside loop bodies."""
    if node is None:
        return

    if isinstance(node, A.Function):
        for stmt in node.body or []:
            check_string_concat_loops(analyzer, stmt, in_loop=False)
        return

    if isinstance(node, _LOOP_NODE_TYPES):
        body = getattr(node, "body", None) or []
        for stmt in body:
            check_string_concat_loops(analyzer, stmt, in_loop=True)
        return

    if isinstance(node, A.If):
        for branch in (node.then_body or [], node.else_body or []):
            for stmt in branch:
                check_string_concat_loops(analyzer, stmt, in_loop=in_loop)
        return
    if isinstance(node, A.Match):
        for _, case_body in node.cases or []:
            for stmt in case_body:
                check_string_concat_loops(analyzer, stmt, in_loop=in_loop)
        for stmt in node.default_case or []:
            check_string_concat_loops(analyzer, stmt, in_loop=in_loop)
        return

    if in_loop and isinstance(node, A.Assign):
        value = node.value
        if isinstance(value, A.BinaryOp) and value.op == "+":
            other = _self_concat_other(value, node.var_name)
            if other is not None and _looks_like_string(other):
                line = getattr(node, "line", 0)
                col = getattr(node, "column", 0)
                analyzer.warnings.append(
                    AnalysisWarning(
                        line=line,
                        column=col,
                        category="perf",
                        message=(
                            f"`{node.var_name} = {node.var_name} + ...` "
                            "inside a loop is O(n^2) for strings -- each "
                            "iteration mallocs a fresh buffer and copies "
                            "the entire prior string."
                        ),
                        suggestion=(
                            "Build with str_array_push into a str_array_new(cap), "
                            'then str_array_join(arr, "") once at the end. O(n) '
                            "total, single allocation."
                        ),
                        severity="warning",
                    )
                )
        return

    for attr in ("body", "then_body", "else_body"):
        sub = getattr(node, attr, None)
        if isinstance(sub, list):
            for stmt in sub:
                check_string_concat_loops(analyzer, stmt, in_loop=in_loop)
