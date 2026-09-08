"""Fixed-integer assignment lowering helpers."""

from __future__ import annotations

from parser import ast as A
from parser.ast import parsed_type_to_str

from transpiler.arithmetic_literal_proofs import int_literal_value
from transpiler.fixed_int_types import (
    c_name_for_fixed,
    info_for_c_fixed,
    info_for_fixed_int,
)


def _fixed_int_info_for_ailang_spec(self, type_spec: object):
    try:
        resolved = self._resolve_type_alias_spec(parsed_type_to_str(type_spec))
    except Exception:
        resolved = parsed_type_to_str(type_spec)
    return info_for_fixed_int(resolved)


def _c_fixed_literal_expr(value: int, target_info) -> str:
    """Build an exact C expression for a fixed-width integer literal.

    Host C integer suffixes stop at 64 bits.  Construct wider constants from
    64-bit chunks *after* casting each chunk to the target-width unsigned type;
    this avoids the historical `...ULL` truncation before i128..i8192 casts.
    """
    bits = int(target_info.bits)
    if bits <= 64:
        if value < 0:
            return str(value) + "LL"
        return f"0x{value:X}ULL" if value > 0x7FFFFFFF else f"{value}LL"
    unsigned_info = info_for_fixed_int(f"u{bits}")
    if unsigned_info is None:
        raise ValueError(f"missing unsigned fixed-int metadata for u{bits}")
    uc = c_name_for_fixed(unsigned_info)
    bit_pattern = value & ((1 << bits) - 1)
    terms: list[str] = []
    for shift in range(0, bits, 64):
        chunk = (bit_pattern >> shift) & ((1 << 64) - 1)
        if chunk == 0:
            continue
        term = f"(({uc})0x{chunk:X}ULL)"
        if shift:
            term = f"({term} << {shift})"
        terms.append(term)
    expr = " | ".join(terms) if terms else f"({uc})0"
    target_c = c_name_for_fixed(target_info)
    return f"(({target_c})({expr}))"


def _emit_checked_fixed_int_assignment(
    self,
    target: str,
    target_spec: object,
    value_node: A.ASTNode,
    value_code: str,
) -> bool:
    """Assign one fixed integer to another without implicit C wrapping.

    The C backend uses native C/_BitInt values, whose implicit conversions are
    allowed to truncate or reinterpret.  AILang's safe implicit conversion
    contract is stricter: if the runtime value is not representable in the
    declared target type, trap before the cast.
    """
    target_info = _fixed_int_info_for_ailang_spec(self, target_spec)
    source_c = self._infer_type(value_node)
    source_info = info_for_c_fixed(source_c)
    if target_info is None or source_info is None:
        return False

    target_c = self._ailang_type_to_c(parsed_type_to_str(target_spec))
    literal = int_literal_value(value_node)
    if literal is not None:
        low = 0 if target_info.unsigned else -(1 << (target_info.bits - 1))
        high = (
            (1 << target_info.bits) - 1
            if target_info.unsigned
            else (1 << (target_info.bits - 1)) - 1
        )
        if low <= literal <= high:
            # Literals are adaptable to their declared fixed type.  Keep the
            # full target width in C rather than materializing through int64_t.
            source_info = target_info
            source_c = target_c
            value_code = _c_fixed_literal_expr(literal, target_info)
    sw, tw = source_info.bits, target_info.bits
    su, tu = source_info.unsigned, target_info.unsigned
    checks: list[str] = []
    temp = "__ailang_int_conv"

    if tu:
        if not su:
            checks.append(f"{temp} < 0")
        if tw < sw:
            # Short-circuiting after the negative check keeps signed right
            # shift confined to non-negative values.
            checks.append(f"({temp} >> {tw}) != 0")
    else:
        if su:
            # A signed iN has N-1 value bits.  Any higher source bit means the
            # unsigned value does not fit the positive half of the target.
            if tw <= sw:
                checks.append(f"({temp} >> {tw - 1}) != 0")
        elif tw < sw:
            low = f"-((({source_c})1) << {tw - 1})"
            high = f"(((({source_c})1) << {tw - 1}) - 1)"
            checks.extend([f"{temp} < {low}", f"{temp} > {high}"])

    self.emit("{")
    self.emit(f"  {source_c} {temp} = ({source_c})({value_code});")
    if checks:
        cond = " || ".join(f"({check})" for check in checks)
        target_name = f"{'u' if tu else 'i'}{tw}"
        self.emit(f"  if ({cond}) {{")
        self.emit(
            f'    fprintf(stderr, "Error: integer value does not fit {target_name}!\\n");'
        )
        self.emit(
            f'    __ailang_safety_trap("integer conversion out of range for {target_name}");'
        )
        self.emit("  }")
    self.emit(f"  {target} = ({target_c}){temp};")
    self.emit("}")
    return True
