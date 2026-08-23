"""Local scalar constant invalidation helpers for LLVM lowering."""

from __future__ import annotations

from parser import ast as A


def snapshot_local_constants(codegen) -> dict:
    constants = getattr(codegen, "local_constant_values", None)
    return dict(constants) if isinstance(constants, dict) else {}


def restore_local_constants(codegen, snapshot: dict) -> None:
    constants = getattr(codegen, "local_constant_values", None)
    if isinstance(constants, dict):
        constants.clear()
        constants.update(snapshot)


def clear_local_constants(codegen) -> None:
    constants = getattr(codegen, "local_constant_values", None)
    if isinstance(constants, dict):
        constants.clear()


def forget_local_constants(codegen, names: set[str]) -> None:
    constants = getattr(codegen, "local_constant_values", None)
    if not isinstance(constants, dict):
        return
    for name in names:
        constants.pop(name, None)


def loop_assigned_names(node) -> set[str]:
    """Return names mutated inside a loop body/step.

    Constants assigned before the loop, such as an invariant modulus, can stay
    usable inside the loop when the loop does not write them.
    """
    names: set[str] = set()
    if isinstance(node, A.For):
        _collect_names(node.body, names)
        _collect_names(node.step, names)
    elif isinstance(node, A.Foreach):
        names.add(node.var_name)
        _collect_names(node.body, names)
    else:
        _collect_names(getattr(node, "body", None), names)
    return names


def branch_assigned_names(node: A.If) -> set[str]:
    names: set[str] = set()
    _collect_names(node.then_body, names)
    _collect_names(node.else_body, names)
    return names


def _collect_names(node, names: set[str]) -> None:
    if node is None:
        return
    if isinstance(node, list):
        for item in node:
            _collect_names(item, names)
        return
    if isinstance(node, A.Assign):
        names.add(node.var_name)
        return
    if isinstance(node, A.VarDecl):
        names.add(node.var_name)
        return
    if isinstance(node, A.RangeVarDecl):
        names.add(node.var_name)
        return
    if isinstance(node, A.TupleAssign):
        names.update(node.var_names)
        return
    if isinstance(node, A.For):
        _collect_names(node.init, names)
        _collect_names(node.body, names)
        _collect_names(node.step, names)
        return
    if isinstance(node, A.While):
        _collect_names(node.body, names)
        return
    if isinstance(node, A.DoWhile):
        _collect_names(node.body, names)
        return
    if isinstance(node, A.Loop):
        _collect_names(node.body, names)
        return
    if isinstance(node, A.Repeat):
        _collect_names(node.body, names)
        return
    if isinstance(node, A.Foreach):
        names.add(node.var_name)
        _collect_names(node.body, names)
        return
    if isinstance(node, A.If):
        _collect_names(node.then_body, names)
        _collect_names(node.else_body, names)
        return
    _collect_generic_bodies(node, names)


def _collect_generic_bodies(node, names: set[str]) -> None:
    for attr in ("body", "then_body", "else_body", "finally_body"):
        if hasattr(node, attr):
            _collect_names(getattr(node, attr), names)
    for attr in ("except_bodies", "catch_bodies", "handlers"):
        value = getattr(node, attr, None)
        if isinstance(value, dict):
            for body in value.values():
                _collect_names(body, names)
        else:
            _collect_names(value, names)
