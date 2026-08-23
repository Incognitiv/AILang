"""LLVM scalar replacement for non-escaping literal dictionaries."""

from __future__ import annotations

import hashlib
from parser import ast as A
from typing import Any

from llvmlite import ir
from transpiler.codegen_int_ranges import remember_fixed_dict_range
from transpiler.dict_specialization import fixed_dict_literal_slots


def _literal_key(node: Any) -> str | None:
    if isinstance(node, A.StringLit):
        return node.value
    return None


def _scan_literal_dict_locals(body: list[Any]) -> set[str]:
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, A.Assign) and isinstance(node.value, A.DictLit):
            names.add(node.var_name)
        elif isinstance(node, A.VarDecl) and isinstance(node.init_value, A.DictLit):
            names.add(node.var_name)
        for value in vars(node).values() if hasattr(node, "__dict__") else ():
            if isinstance(value, A.ASTNode):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, A.ASTNode):
                        walk(item)
                    elif isinstance(item, tuple):
                        for child in item:
                            if isinstance(child, A.ASTNode):
                                walk(child)

    for stmt in body:
        walk(stmt)
    return names


def analyze_llvm_fixed_dicts(body: list[Any]) -> dict[str, set[str]]:
    """Return local dicts safe to scalarize to stack slots in LLVM IR."""
    candidates = _scan_literal_dict_locals(body)
    slots = fixed_dict_literal_slots(body, candidates)
    return {name: set(keys) for name, keys in slots.items()}


def _slot_name(var_name: str, key: str) -> str:
    digest = hashlib.blake2s(f"{var_name}:{key}".encode(), digest_size=4).hexdigest()
    safe_var = "".join(ch if ch.isalnum() else "_" for ch in var_name)
    return f"fdict_{safe_var}_{digest}"


def emit_fixed_dict_init(cg: Any, var_name: str, node: Any) -> bool:
    """Emit stack scalar storage for an eligible literal dict assignment."""
    keys_by_var = getattr(cg, "_llvm_fixed_dict_keys", {})
    keys = keys_by_var.get(var_name)
    if not keys or not isinstance(node, A.DictLit):
        return False

    i64 = ir.IntType(64)
    values = getattr(cg, "_llvm_fixed_dict_values", {})
    slots: dict[str, ir.Value] = {}
    for key in sorted(keys):
        slot = cg.alloca_in_entry_block(i64, _slot_name(var_name, key))
        cg.current_builder.store(ir.Constant(i64, 0), slot)
        slots[key] = slot

    for key_expr, value_expr in node.pairs:
        key = _literal_key(key_expr)
        if key not in slots:
            return False
        value = cg.generate_expr(value_expr)
        value_i64 = cg.expr_generator._convert_dict_value(value)
        cg.current_builder.store(value_i64, slots[key])
        remember_fixed_dict_range(cg, var_name, key, value_expr)

    values[var_name] = slots
    cg._llvm_fixed_dict_values = values
    cg.local_decl_types[var_name] = "dict"
    cg.var_signedness[var_name] = True
    cg.array_metadata.pop(var_name, None)
    return True


def try_fixed_dict_access(cg: Any, dict_expr: Any, key_expr: Any) -> ir.Value | None:
    if not isinstance(dict_expr, A.Variable):
        return None
    key = _literal_key(key_expr)
    if key is None:
        return None
    slot = getattr(cg, "_llvm_fixed_dict_values", {}).get(dict_expr.name, {}).get(key)
    if slot is None:
        return None
    return cg.current_builder.load(slot, name=f"{dict_expr.name}_{key}_val")


def try_fixed_dict_assign(cg: Any, node: Any) -> bool:
    if not isinstance(node, A.DictAssign):
        return False
    if not isinstance(node.dict_expr, A.Variable):
        return False
    key = _literal_key(node.key_expr)
    if key is None:
        return False
    slot = (
        getattr(cg, "_llvm_fixed_dict_values", {}).get(node.dict_expr.name, {}).get(key)
    )
    if slot is None:
        return False
    value = cg.generate_expr(node.value_expr)
    value_i64 = cg.expr_generator._convert_dict_value(value)
    cg.current_builder.store(value_i64, slot)
    remember_fixed_dict_range(cg, node.dict_expr.name, key, node.value_expr)
    return True
