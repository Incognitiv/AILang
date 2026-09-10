"""Shared LLVM-side fixed integer contract helpers."""

from __future__ import annotations

from parser.ast import parsed_type_to_str

from llvmlite import ir
from transpiler.fixed_int_types import info_for_fixed_int


def fixed_int_info_for_spec(codegen, type_spec):
    try:
        resolved = codegen._resolve_type_alias_spec(type_spec)
    except Exception:
        resolved = type_spec
    return info_for_fixed_int(parsed_type_to_str(resolved))


def cast_to_declared_int(codegen, value: ir.Value, target_type: ir.Type, type_spec):
    info = fixed_int_info_for_spec(codegen, type_spec)
    if info is None or not isinstance(target_type, ir.IntType):
        return codegen.cast_value(value, target_type)
    return codegen.cast_value(value, target_type, target_unsigned=info.unsigned)
