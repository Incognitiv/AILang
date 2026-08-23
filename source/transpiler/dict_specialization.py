from __future__ import annotations

from parser import ast as A
from typing import Dict, List, Optional, Set

UINT64_MASK = (1 << 64) - 1


def _dict_stack_capacity(pair_count: int) -> int:
    capacity = 16
    while pair_count > (capacity * 3) // 4:
        capacity *= 2
    return capacity


def dict_literal_stack_capacity(pair_count: int) -> int:
    return _dict_stack_capacity(pair_count)


def _hash_literal_key(key: str) -> int:
    if len(key) == 1:
        return ord(key)
    h = 5381
    for ch in key:
        h = (((h << 5) + h) + ord(ch)) & UINT64_MASK
    return h


def _literal_key(node: A.ASTNode) -> Optional[str]:
    if isinstance(node, A.StringLit):
        return node.value
    return None


def _literal_dict_slots(node: A.DictLit) -> Optional[Dict[str, int]]:
    keys: List[str] = []
    for key, _value in node.pairs:
        literal = _literal_key(key)
        if literal is None:
            return None
        if literal not in keys:
            keys.append(literal)
    capacity = _dict_stack_capacity(len(node.pairs))
    slots: Dict[str, int] = {}
    occupied: Set[int] = set()
    for key in keys:
        slot = _hash_literal_key(key) & (capacity - 1)
        while slot in occupied:
            slot = (slot + 1) & (capacity - 1)
        occupied.add(slot)
        slots[key] = slot
    return slots


def fixed_dict_literal_slots(
    body: List[A.ASTNode], tracked_dicts: Set[str]
) -> Dict[str, Dict[str, int]]:
    literal_inits: Dict[str, A.DictLit] = {}
    invalid: Set[str] = set()

    def note_init(var_name: str, value: A.ASTNode) -> None:
        if var_name not in tracked_dicts:
            return
        if not isinstance(value, A.DictLit):
            invalid.add(var_name)
            return
        if var_name in literal_inits:
            invalid.add(var_name)
            return
        if _literal_dict_slots(value) is None:
            invalid.add(var_name)
            return
        literal_inits[var_name] = value

    def check_expr(expr: A.ASTNode) -> None:
        if expr is None:
            return
        if isinstance(expr, A.ArrayAccess):
            if isinstance(expr.array, A.Variable) and expr.array.name in tracked_dicts:
                key = _literal_key(expr.index)
                if key is None or key not in _keys_for(expr.array.name):
                    invalid.add(expr.array.name)
                return
        if isinstance(expr, A.DictAccess):
            if (
                isinstance(expr.dict_expr, A.Variable)
                and expr.dict_expr.name in tracked_dicts
            ):
                key = _literal_key(expr.key_expr)
                if key is None or key not in _keys_for(expr.dict_expr.name):
                    invalid.add(expr.dict_expr.name)
                return
        if isinstance(expr, A.Variable) and expr.name in tracked_dicts:
            invalid.add(expr.name)
            return
        if isinstance(expr, A.Call):
            for arg in expr.args or []:
                if isinstance(arg, A.Variable) and arg.name in tracked_dicts:
                    invalid.add(arg.name)
                else:
                    check_expr(arg)
            return
        if isinstance(expr, A.MethodCall):
            if (
                isinstance(expr.object_expr, A.Variable)
                and expr.object_expr.name in tracked_dicts
            ):
                invalid.add(expr.object_expr.name)
            else:
                check_expr(expr.object_expr)
            for arg in expr.args or []:
                check_expr(arg)
            return
        for attr in (
            "left",
            "right",
            "true_expr",
            "false_expr",
            "cond",
            "object_expr",
            "array",
            "index",
            "dict_expr",
            "key_expr",
            "expr",
            "value",
            "iterable",
        ):
            sub = getattr(expr, attr, None)
            if sub is not None and not isinstance(sub, (str, int, bool, float)):
                check_expr(sub)
        for attr in ("args", "elements", "parts"):
            seq = getattr(expr, attr, None)
            if isinstance(seq, list):
                for child in seq:
                    if not isinstance(child, (str, int, bool, float, tuple)):
                        check_expr(child)

    def _keys_for(var_name: str) -> Set[str]:
        init = literal_inits.get(var_name)
        if init is None:
            return set()
        return {key.value for key, _value in init.pairs if isinstance(key, A.StringLit)}

    def walk(node: A.ASTNode) -> None:
        if node is None:
            return
        if isinstance(node, A.Assign):
            note_init(node.var_name, node.value)
            if not isinstance(node.value, A.DictLit):
                check_expr(node.value)
        elif isinstance(node, A.VarDecl) and node.init_value is not None:
            note_init(node.var_name, node.init_value)
            if not isinstance(node.init_value, A.DictLit):
                check_expr(node.init_value)
        elif isinstance(node, A.DictAssign):
            if (
                isinstance(node.dict_expr, A.Variable)
                and node.dict_expr.name in tracked_dicts
            ):
                key = _literal_key(node.key_expr)
                if key is None or key not in _keys_for(node.dict_expr.name):
                    invalid.add(node.dict_expr.name)
            else:
                check_expr(node.dict_expr)
            check_expr(node.value_expr)
        else:
            for attr in ("value", "init_value", "cond", "init", "step"):
                sub = getattr(node, attr, None)
                if sub is not None:
                    check_expr(sub)
        for attr in ("body", "then_body", "else_body", "try_body", "finally_body"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                for stmt in sub:
                    walk(stmt)
        if isinstance(node, A.TryExcept):
            for _err_type, _var_name, catch_body in node.catch_blocks:
                for stmt in catch_body:
                    walk(stmt)
            if node.except_block:
                _err_var, except_body = node.except_block
                for stmt in except_body:
                    walk(stmt)
        elsif = getattr(node, "elsif_branches", None)
        if elsif:
            for _cond, branch in elsif:
                if isinstance(branch, list):
                    for stmt in branch:
                        walk(stmt)

    for stmt in body:
        walk(stmt)
    return {
        var_name: slots
        for var_name, init in literal_inits.items()
        if var_name not in invalid and (slots := _literal_dict_slots(init)) is not None
    }
