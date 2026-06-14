"""Virtual string materialization analysis.

This pass finds the safe subset where a string field only needs its byte length.
Those fields can receive a virtual string as NULL + cached length, avoiding a
temporary char* allocation without changing observable string behavior elsewhere.
"""

from __future__ import annotations

from parser import ast as A
from typing import Any

from ast_access import arg_at
from transpiler.class_field_ownership import is_string_type

VirtualStringAnalysis = tuple[set[tuple[str, str]], set[tuple[str, str, int]]]


def analyze_virtual_string_materialization(
    nodes: list[A.ASTNode],
    classes: dict[str, Any],
) -> VirtualStringAnalysis:
    """Return `(length_only_fields, elidable_string_params)`.

    `length_only_fields` contains `(Class, field)` pairs whose reads are only
    through `strlen`/`len`. `elidable_string_params` contains
    `(Class, method, param_index)` pairs where a virtual string argument may be
    passed as NULL because the method only transfers that parameter into a
    length-only string field or asks for its length.
    """
    string_fields = _collect_string_fields(classes)
    length_only = set(string_fields)
    for node in nodes:
        _scan_field_reads(node, classes, string_fields, length_only, None, False)

    elidable_params: set[tuple[str, str, int]] = set()
    for class_name, class_info in classes.items():
        _fields, methods = class_info if class_info else ([], [])
        for method in methods:
            params = method.params or []
            for index, param in enumerate(params):
                if not (isinstance(param, tuple) and len(param) >= 2):
                    continue
                if not is_string_type(param[1]):
                    continue
                pname = str(param[0])
                if _param_is_virtual_transfer_only(
                    method.body or [], pname, class_name, length_only
                ):
                    elidable_params.add((class_name, method.name, index))
    return length_only, elidable_params


def _collect_string_fields(classes: dict[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for class_name, class_info in classes.items():
        fields = class_info[0] if class_info else []
        for field in fields:
            if not isinstance(field, tuple) or len(field) < 3:
                continue
            _vis, fname, ftype = field[:3]
            if is_string_type(ftype):
                result.add((class_name, str(fname)))
    return result


def _scan_field_reads(
    node: Any,
    classes: dict[str, Any],
    string_fields: set[tuple[str, str]],
    length_only: set[tuple[str, str]],
    current_class: str | None,
    len_context: bool,
) -> None:
    if node is None:
        return
    if isinstance(node, A.ClassDef):
        for method in node.methods:
            _scan_field_reads(
                method, classes, string_fields, length_only, node.name, False
            )
        return
    if isinstance(node, A.Function):
        for stmt in node.body or []:
            _scan_field_reads(
                stmt, classes, string_fields, length_only, current_class, False
            )
        return
    if isinstance(node, A.Call):
        if node.name in {"strlen", "len"} and node.args:
            _scan_field_reads(
                arg_at(node, 0),
                classes,
                string_fields,
                length_only,
                current_class,
                True,
            )
            for arg in node.args[1:]:
                _scan_field_reads(
                    arg, classes, string_fields, length_only, current_class, False
                )
            return
    if isinstance(node, A.FieldAccess):
        targets = _field_targets(node, classes, string_fields, current_class)
        if not len_context:
            length_only.difference_update(targets)
        _scan_field_reads(
            node.object_expr, classes, string_fields, length_only, current_class, False
        )
        return
    if isinstance(node, A.FieldAssign):
        _scan_field_reads(
            node.object_expr, classes, string_fields, length_only, current_class, False
        )
        _scan_field_reads(
            node.value, classes, string_fields, length_only, current_class, False
        )
        return
    if isinstance(node, A.MethodCall):
        _scan_field_reads(
            node.object_expr, classes, string_fields, length_only, current_class, False
        )
        for arg in node.args or []:
            _scan_field_reads(
                arg, classes, string_fields, length_only, current_class, False
            )
        return
    if isinstance(node, A.ASTNode):
        for child in vars(node).values():
            _scan_field_reads(
                child, classes, string_fields, length_only, current_class, False
            )
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_field_reads(
                item, classes, string_fields, length_only, current_class, False
            )
    elif isinstance(node, dict):
        for item in node.values():
            _scan_field_reads(
                item, classes, string_fields, length_only, current_class, False
            )


def _field_targets(
    node: A.FieldAccess,
    classes: dict[str, Any],
    string_fields: set[tuple[str, str]],
    current_class: str | None,
) -> set[tuple[str, str]]:
    if isinstance(node.object_expr, A.ThisExpr) and current_class is not None:
        target = (current_class, node.field_name)
        return {target} if target in string_fields else set()
    return {
        (class_name, field_name)
        for class_name, field_name in string_fields
        if field_name == node.field_name
    }


def _param_is_virtual_transfer_only(
    body: list[A.ASTNode],
    param_name: str,
    class_name: str,
    length_only: set[tuple[str, str]],
) -> bool:
    seen_transfer = False

    def walk(node: Any, allowed: bool = False) -> bool:
        nonlocal seen_transfer
        if node is None:
            return True
        if isinstance(node, A.Variable) and node.name == param_name:
            return allowed
        if isinstance(node, A.Call):
            if node.name in {"strlen", "len"} and node.args:
                if (
                    isinstance(arg_at(node, 0), A.Variable)
                    and arg_at(node, 0).name == param_name
                ):
                    return all(walk(arg, False) for arg in node.args[1:])
            return all(walk(arg, False) for arg in node.args or [])
        if isinstance(node, A.FieldAssign):
            if (
                isinstance(node.object_expr, A.ThisExpr)
                and (class_name, node.field_name) in length_only
                and isinstance(node.value, A.Variable)
                and node.value.name == param_name
            ):
                seen_transfer = True
                return walk(node.object_expr, False)
            return walk(node.object_expr, False) and walk(node.value, False)
        if isinstance(node, A.ASTNode):
            return all(walk(child, False) for child in vars(node).values())
        if isinstance(node, (list, tuple)):
            return all(walk(item, False) for item in node)
        if isinstance(node, dict):
            return all(walk(item, False) for item in node.values())
        return True

    return all(walk(stmt, False) for stmt in body) and seen_transfer
