"""Proof-backed integer narrowing helpers for LLVM codegen.

AILang keeps ``int`` as the language-level 64-bit default. These helpers only
allow LLVM to use narrower internal SSA/storage where range facts prove safety.
"""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str
from typing import Any

from abi_symbols import explicit_c_abi_parts, has_export_decorator
from llvmlite import ir
from transpiler.local_int_narrowing import param_uses_are_i32_storage_safe

I32_LOW = -(1 << 31)
I32_HIGH = (1 << 31) - 1


def maybe_narrow_local_type(
    codegen: Any,
    name: str,
    llvm_type: ir.Type,
    *,
    hint_range: tuple[int, int] | None = None,
) -> ir.Type:
    """Keep language-level integer locals at their declared storage width.

    A range of stored values alone cannot prove that narrowing is safe: later
    arithmetic may need the full width, and ``addressof`` exposes the physical
    slot size. Explicit ``i32``/``u32`` declarations already arrive as i32;
    range variables use their own lowering path.
    """

    _ = hint_range  # Retained for the existing codegen call-site contract.
    return llvm_type


def maybe_narrow_param_value(
    codegen: Any,
    node: A.Function,
    index: int,
    name: str,
    type_spec: Any,
    value: ir.Value,
    mutated: bool,
) -> ir.Value:
    """Narrow a read-only private parameter value to i32 when proven safe."""

    if mutated or not _can_narrow_param(codegen, node):
        return value
    declared = _declared_param_range(codegen, type_spec)
    if not _param_type_can_narrow(type_spec, declared) or not _is_i64_type(value.type):
        return value
    hints = getattr(getattr(codegen, "range_facts", None), "call_arg_ranges", {})
    hint = hints.get(node.name, {}).get(index)
    if hint is None:
        hint = declared
    if not _fits_i32_interval(hint):
        return value
    if declared is not None and not _fits_i32_interval(declared):
        return value
    scoped = _scope_interval(codegen, name)
    if scoped is not None and not _fits_i32_interval(scoped):
        return value
    if not param_uses_are_i32_storage_safe(node.body, name):
        return value
    return codegen.builder.trunc(value, ir.IntType(32), name=f"{name}_i32")


def effective_local_type_name(
    llvm_type: ir.Type, fallback: str | None = None
) -> str | None:
    """Return a declaration type override for narrowed integer locals."""

    if isinstance(llvm_type, ir.IntType) and llvm_type.width == 32:
        if fallback is not None and fallback.strip().lower().startswith("u"):
            return fallback
        return "i32"
    return fallback


def cast_for_narrowed_storage(
    codegen: Any, value: ir.Value, target_type: ir.Type
) -> ir.Value:
    """Cast into narrowed storage while preserving integer constants."""

    if value.type == target_type:
        return value
    if isinstance(value, ir.Constant) and isinstance(target_type, ir.IntType):
        if isinstance(value.type, ir.IntType):
            raw = getattr(value, "constant", None)
            if raw is not None:
                return ir.Constant(target_type, int(raw))
    return codegen.cast_value(value, target_type)


def _can_narrow_param(codegen: Any, node: A.Function) -> bool:
    if node.name == "main":
        return False
    decorators = getattr(node, "decorators", [])
    if explicit_c_abi_parts(decorators) is not None:
        return False
    if has_export_decorator(decorators):
        return False
    if node.name in getattr(codegen, "_fn_ptr_function_names", set()):
        return False
    if node.name in getattr(codegen, "_recursive_functions", set()):
        return False
    return True


def _declared_param_range(codegen: Any, type_spec: Any) -> tuple[int, int] | None:
    helper = getattr(codegen, "_declared_param_range", None)
    if helper is None:
        return None
    try:
        found = helper(type_spec)
    except Exception:
        return None
    if found is None:
        return None
    return int(found[0]), int(found[1])


def _param_type_can_narrow(type_spec: Any, declared: tuple[int, int] | None) -> bool:
    if declared is not None:
        return True
    return _is_i64ish_type_name(type_spec)


def _scope_interval(codegen: Any, name: str) -> object | None:
    facts = getattr(codegen, "range_facts", None)
    current = getattr(codegen, "_current_function_name", None)
    if facts is None or current is None:
        return None
    return getattr(facts, "scope_ranges", {}).get(current, {}).get(name)


def _is_i64_type(llvm_type: ir.Type) -> bool:
    return isinstance(llvm_type, ir.IntType) and llvm_type.width == 64


def _is_i64ish_type_name(type_spec: Any) -> bool:
    text = parsed_type_to_str(type_spec).strip().lower()
    return text in {"int", "i64", "int64_t", "long long", "signed long long"}


def _fits_i32_interval(interval: object | None) -> bool:
    if interval is None:
        return False
    if isinstance(interval, tuple) and len(interval) == 2:
        low, high = interval
    else:
        low = getattr(interval, "low", None)
        high = getattr(interval, "high", None)
    if low is None or high is None:
        return False
    return I32_LOW <= int(low) and int(high) <= I32_HIGH
