"""Checked C-expression lowering for AILang fixed integer conversions.

Function-call and return boundaries are expression contexts, so they cannot use
statement-only local-assignment checks.  This module selects a runtime helper
that preserves the *numeric value* across signedness/width changes and traps
when the destination iN/uN cannot represent it.
"""
from __future__ import annotations

from parser.ast import parsed_type_to_str
from transpiler.fixed_int_types import info_for_c_fixed, info_for_fixed_int


def _target_info(transpiler, type_spec: object):
    try:
        resolved = transpiler._resolve_type_alias_spec(parsed_type_to_str(type_spec))
    except Exception:
        resolved = parsed_type_to_str(type_spec)
    return info_for_fixed_int(resolved)


def checked_fixed_int_conversion_expr(
    transpiler, value_node, value_code: str, target_spec: object
) -> str | None:
    """Return a checked conversion expression, or ``None`` when not applicable."""
    target = _target_info(transpiler, target_spec)
    source = info_for_c_fixed(transpiler._infer_type(value_node))
    if target is None or source is None:
        return None
    if source.bits == target.bits and source.unsigned == target.unsigned:
        return value_code

    max_bits = max(source.bits, target.bits)
    if max_bits <= 64:
        family = 64
        source_c = "uint64_t" if source.unsigned else "int64_t"
    elif max_bits <= 128:
        family = 128
        source_c = "unsigned __int128" if source.unsigned else "__int128"
    else:
        family = 8192
        source_c = "ailang_u8192" if source.unsigned else "ailang_i8192"

    source_tag = "u" if source.unsigned else "s"
    return (
        f"ailang_cast_{target.canonical}_from_{source_tag}{family}"
        f"(({source_c})({value_code}))"
    )
