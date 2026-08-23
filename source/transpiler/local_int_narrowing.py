"""Proof-backed integer storage narrowing for generated C.

The language-level default integer stays 64-bit. This module only changes
private C storage when range facts prove the value set fits a narrower type.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any, Dict, Iterable, List, Optional, Tuple

from abi_symbols import explicit_c_abi_parts, has_export_decorator

I32_LOW = -(1 << 31)
I32_HIGH = (1 << 31) - 1


def apply_proven_i32_narrowing(
    owner: Any, node: A.Function, all_vars: Dict[str, str]
) -> Dict[str, str]:
    """Narrow private i64 params/locals to i32 where range facts prove safety."""

    facts = getattr(owner, "range_facts", None)
    if facts is None:
        return {}

    param_overrides: Dict[str, str] = {}
    if _can_narrow_function(owner, node):
        param_overrides = _narrow_params(owner, node, facts)

    local_overrides = _narrow_locals(owner, node, facts, all_vars)
    for name in local_overrides:
        all_vars[name] = "i32"
        owner._var_types[name] = "i32"

    return param_overrides


def apply_proven_i32_signature_narrowing(
    owner: Any, nodes: Iterable[A.ASTNode]
) -> None:
    """Narrow private function signatures before prologue emission."""

    facts = getattr(owner, "range_facts", None)
    if facts is None:
        return
    for node in nodes:
        if not isinstance(node, A.Function) or not _can_narrow_function(owner, node):
            continue
        _narrow_params(owner, node, facts, mutate_ast=True)


def _can_narrow_function(owner: Any, node: A.Function) -> bool:
    if node.name == "main":
        return False
    if explicit_c_abi_parts(getattr(node, "decorators", [])) is not None:
        return False
    if has_export_decorator(getattr(node, "decorators", [])):
        return False
    if node.name in getattr(owner, "_fn_ptr_function_names", set()):
        return False
    if node.name in getattr(owner, "_recursive_funcs", set()):
        return False
    return True


def _narrow_params(
    owner: Any, node: A.Function, facts: Any, mutate_ast: bool = False
) -> Dict[str, str]:
    hints = getattr(facts, "call_arg_ranges", {}).get(node.name, {})
    if not hints:
        return {}
    scope_ranges = getattr(facts, "scope_ranges", {}).get(node.name, {})
    assigned = _assigned_names(node.body)
    overrides: Dict[str, str] = {}
    param_types: List[str] = []
    current = getattr(owner, "functions", {}).get(node.name)
    if current is not None:
        param_types = list(current[0])

    for index, param in enumerate(node.params or []):
        pname, ptype = _param_name_type(param)
        if pname is None or not _is_i64ish(ptype):
            continue
        if pname in assigned:
            continue
        if not _param_uses_are_i32_storage_safe(node.body, pname):
            continue
        hint = hints.get(index)
        if not _fits_i32_interval(hint):
            continue
        scoped = scope_ranges.get(pname)
        if scoped is not None and not _fits_i32_interval(scoped):
            continue
        overrides[pname] = "i32"
        owner._var_types[pname] = "i32"
        if index < len(param_types):
            param_types[index] = "i32"
        if mutate_ast:
            _set_param_type(node, index, "i32")

    if overrides and current is not None:
        owner.functions[node.name] = (param_types, current[1])
    return overrides


def _narrow_locals(
    owner: Any, node: A.Function, facts: Any, all_vars: Dict[str, str]
) -> Tuple[str, ...]:
    """Keep default integer locals at their language-level storage width.

    Stored-value ranges do not prove that all uses fit i32, and taking a
    variable's address makes its storage width observable. Explicit narrow
    declarations remain narrow; this only disables inferred local narrowing.
    """

    return ()


def _param_name_type(param: Any) -> Tuple[Optional[str], str]:
    if isinstance(param, tuple):
        if not param:
            return None, ""
        ptype = param[1] if len(param) > 1 else "int"
        return str(param[0]), parsed_type_to_str(ptype)
    return str(param), "int"


def _set_param_type(node: A.Function, index: int, new_type: str) -> None:
    param = node.params[index]
    if not isinstance(param, tuple):
        return
    if len(param) >= 3:
        node.params[index] = (param[0], new_type, param[2])
    elif len(param) == 2:
        node.params[index] = (param[0], new_type)
    elif len(param) == 1:
        node.params[index] = (param[0], new_type, None)


def _is_i64ish(type_name: object) -> bool:
    lowered = str(type_name).strip().lower()
    return lowered in {"int", "i64", "int64_t", "long long", "signed long long"}


def _fits_i32_interval(interval: object) -> bool:
    if interval is None:
        return False
    low = getattr(interval, "low", None)
    high = getattr(interval, "high", None)
    if low is None or high is None:
        return False
    return I32_LOW <= int(low) and int(high) <= I32_HIGH


def _assigned_names(body: Iterable[A.ASTNode]) -> set[str]:
    assigned: set[str] = set()
    for node in _walk_nodes(body):
        if isinstance(node, A.Assign):
            assigned.add(node.var_name)
        elif isinstance(node, A.VarDecl):
            assigned.add(node.var_name)
        elif isinstance(node, A.RangeVarDecl):
            assigned.add(node.var_name)
    return assigned


def _assignment_values_for(name: str, body: Iterable[A.ASTNode]) -> List[A.ASTNode]:
    values: List[A.ASTNode] = []
    for node in _walk_nodes(body):
        if isinstance(node, A.Assign) and node.var_name == name:
            values.append(node.value)
        elif isinstance(node, A.VarDecl) and node.var_name == name:
            if node.init_value is not None:
                values.append(node.init_value)
        elif isinstance(node, A.RangeVarDecl) and node.var_name == name:
            return []
    return values


def _param_uses_are_i32_storage_safe(
    body: Iterable[A.ASTNode], param_name: str
) -> bool:
    for node in _walk_nodes(body):
        if _node_uses_name_in_unsafe_context(node, param_name):
            return False
    return True


def param_uses_are_i32_storage_safe(body: Iterable[A.ASTNode], param_name: str) -> bool:
    """Return True when a parameter can be represented as i32 internally."""

    return _param_uses_are_i32_storage_safe(body, param_name)


def _node_uses_name_in_unsafe_context(node: A.ASTNode, name: str) -> bool:
    if isinstance(node, A.BinaryOp):
        if node.op in _ARITHMETIC_OPS and _node_contains_var(node, name):
            return True
        return _node_uses_name_in_unsafe_context(
            node.left, name
        ) or _node_uses_name_in_unsafe_context(node.right, name)
    if isinstance(node, A.UnaryOp):
        if node.op in {"-", "+", "~"} and _node_contains_var(node.operand, name):
            return True
        return _node_uses_name_in_unsafe_context(node.operand, name)
    if isinstance(node, A.Call):
        return any(_node_contains_var(arg, name) for arg in node.args)
    if isinstance(node, A.MethodCall):
        return any(_node_contains_var(arg, name) for arg in node.args)
    return False


def _node_contains_var(node: A.ASTNode, name: str) -> bool:
    if isinstance(node, A.Variable):
        return node.name == name
    for value in vars(node).values():
        if isinstance(value, A.ASTNode):
            if _node_contains_var(value, name):
                return True
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, A.ASTNode) and _node_contains_var(item, name):
                    return True
    return False


def _walk_nodes(nodes: Iterable[A.ASTNode]):
    for node in nodes:
        yield node
        for value in vars(node).values():
            if isinstance(value, A.ASTNode):
                yield from _walk_nodes((value,))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, A.ASTNode):
                        yield from _walk_nodes((item,))


def _expr_is_intlike(expr: A.ASTNode) -> bool:
    if isinstance(expr, A.Number):
        return isinstance(expr.value, int)
    if isinstance(expr, A.Variable):
        return True
    if isinstance(expr, A.UnaryOp):
        return _expr_is_intlike(expr.operand)
    if isinstance(expr, A.BinaryOp):
        if expr.op not in _ARITHMETIC_OPS:
            return False
        return _expr_is_intlike(expr.left) and _expr_is_intlike(expr.right)
    if isinstance(expr, A.TernaryOp):
        return _expr_is_intlike(expr.true_expr) and _expr_is_intlike(expr.false_expr)
    return False


_ARITHMETIC_OPS = {
    "+",
    "-",
    "*",
    "%",
    "mod",
    "/",
    "//",
    "<<",
    ">>",
    "&",
    "|",
    "^",
}
