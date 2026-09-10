"""Canonical metadata for AILang's fixed-width integer ladder.

This module is intentionally backend-neutral.  A declared integer type must
carry the same width and signedness through LLVM and C lowering; keeping the
aliases in one table prevents the backends from silently disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FixedIntInfo:
    bits: int
    unsigned: bool
    canonical: str


_ALIASES: dict[str, FixedIntInfo] = {}


def _add(
    bits: int, signed_names: tuple[str, ...], unsigned_names: tuple[str, ...]
) -> None:
    signed = FixedIntInfo(bits, False, f"i{bits}")
    unsigned = FixedIntInfo(bits, True, f"u{bits}")
    for name in signed_names:
        _ALIASES[name] = signed
    for name in unsigned_names:
        _ALIASES[name] = unsigned


_add(8, ("tiny", "i8"), ("byte", "u8"))
_add(16, ("small", "i16"), ("usmall", "u16"))
_add(32, ("short", "i32"), ("ushort", "u32"))
_add(64, ("int", "i64"), ("uint", "u64"))
_add(128, ("long", "i128"), ("ulong", "u128"))
_add(256, ("wide", "i256"), ("uwide", "u256"))
_add(512, ("vast", "i512"), ("uvast", "u512"))
_add(1024, ("grand", "i1024"), ("ugrand", "u1024"))
_add(2048, ("giant", "i2048"), ("ugiant", "u2048"))
_add(4096, ("titan", "i4096"), ("utitan", "u4096"))
_add(8192, ("colos", "i8192"), ("ucolos", "u8192"))


def info_for_fixed_int(type_name: object) -> FixedIntInfo | None:
    text = str(type_name).strip().lower()
    return _ALIASES.get(text)


_C_NAMES: dict[str, FixedIntInfo] = {
    "int8_t": _ALIASES["i8"],
    "uint8_t": _ALIASES["u8"],
    "int16_t": _ALIASES["i16"],
    "uint16_t": _ALIASES["u16"],
    "int32_t": _ALIASES["i32"],
    "uint32_t": _ALIASES["u32"],
    "int64_t": _ALIASES["i64"],
    "uint64_t": _ALIASES["u64"],
    "__int128": _ALIASES["i128"],
    "unsigned __int128": _ALIASES["u128"],
}
for _bits in (256, 512, 1024, 2048, 4096, 8192):
    _C_NAMES[f"ailang_i{_bits}"] = _ALIASES[f"i{_bits}"]
    _C_NAMES[f"ailang_u{_bits}"] = _ALIASES[f"u{_bits}"]


def info_for_c_fixed(c_type: object) -> FixedIntInfo | None:
    return _C_NAMES.get(str(c_type).strip())


def promoted_fixed_info(
    a: FixedIntInfo | None, b: FixedIntInfo | None
) -> FixedIntInfo | None:
    """Lossless common fixed-integer type for backend expression lowering.

    This mirrors AILang's return-type join: mixed signed/unsigned values need
    one extra signed bit to preserve the complete unsigned domain.
    """
    if a is None:
        return b
    if b is None:
        return a
    if a.unsigned == b.unsigned:
        return _ALIASES[f"{'u' if a.unsigned else 'i'}{max(a.bits, b.bits)}"]
    signed_bits = a.bits if not a.unsigned else b.bits
    unsigned_bits = a.bits if a.unsigned else b.bits
    needed = max(signed_bits, unsigned_bits + 1)
    for bits in (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
        if bits >= needed:
            return _ALIASES[f"i{bits}"]
    return None


def c_name_for_fixed(info: FixedIntInfo) -> str:
    if info.bits <= 64:
        return f"uint{info.bits}_t" if info.unsigned else f"int{info.bits}_t"
    if info.bits == 128:
        return "unsigned __int128" if info.unsigned else "__int128"
    return f"ailang_u{info.bits}" if info.unsigned else f"ailang_i{info.bits}"
