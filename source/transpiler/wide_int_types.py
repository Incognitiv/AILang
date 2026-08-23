"""Shared fixed-width integer metadata for the C backend.

AILang's 256..8192-bit integer ladder maps to C23 ``_BitInt`` types on the
C backend.  Keeping this metadata centralized prevents type lowering,
expression emission and printing from disagreeing about widths/signedness.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class WideIntInfo:
    ailang: str
    bits: int
    unsigned: bool
    c_name: str
    suffix: str

_CANON = [
    ("wide", 256, False, "ailang_i256", "i256", ("wide", "i256")),
    ("uwide", 256, True, "ailang_u256", "u256", ("uwide", "u256")),
    ("vast", 512, False, "ailang_i512", "i512", ("vast", "i512")),
    ("uvast", 512, True, "ailang_u512", "u512", ("uvast", "u512")),
    ("grand", 1024, False, "ailang_i1024", "i1024", ("grand", "i1024")),
    ("ugrand", 1024, True, "ailang_u1024", "u1024", ("ugrand", "u1024")),
    ("giant", 2048, False, "ailang_i2048", "i2048", ("giant", "i2048")),
    ("ugiant", 2048, True, "ailang_u2048", "u2048", ("ugiant", "u2048")),
    ("titan", 4096, False, "ailang_i4096", "i4096", ("titan", "i4096")),
    ("utitan", 4096, True, "ailang_u4096", "u4096", ("utitan", "u4096")),
    ("colos", 8192, False, "ailang_i8192", "i8192", ("colos", "i8192")),
    ("ucolos", 8192, True, "ailang_u8192", "u8192", ("ucolos", "u8192")),
]

BY_AILANG: dict[str, WideIntInfo] = {}
BY_C: dict[str, WideIntInfo] = {}
for canonical, bits, unsigned, c_name, suffix, aliases in _CANON:
    info = WideIntInfo(canonical, bits, unsigned, c_name, suffix)
    BY_C[c_name] = info
    for alias in aliases:
        BY_AILANG[alias] = info


def info_for_ailang(type_name: str) -> WideIntInfo | None:
    return BY_AILANG.get(str(type_name).strip().lower())


def info_for_c(c_type: str) -> WideIntInfo | None:
    return BY_C.get(str(c_type).strip())


def promoted_info(a: WideIntInfo | None, b: WideIntInfo | None) -> WideIntInfo | None:
    if a is None:
        return b
    if b is None:
        return a
    bits = max(a.bits, b.bits)
    # For same width, unsigned wins.  When widths differ, preserve the wider
    # operand's signedness; its range dominates the narrower operand in the
    # common QRAX / same-family use case and avoids gratuitous unsigned lifts.
    if a.bits == b.bits:
        unsigned = a.unsigned or b.unsigned
    elif a.bits > b.bits:
        unsigned = a.unsigned
    else:
        unsigned = b.unsigned
    for info in BY_C.values():
        if info.bits == bits and info.unsigned == unsigned:
            return info
    return a if a.bits >= b.bits else b
