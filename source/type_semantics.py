"""Canonical AILang type conversion semantics shared by frontend and IR.

This module deliberately contains no parser or backend dependencies.  It is the
language-level contract for deciding whether a conversion is identity,
lossless, checked at runtime, explicitly lossy, or forbidden.  Backends may
implement an accepted conversion differently, but they must not silently widen
the language contract.
"""

from __future__ import annotations

import re
from enum import Enum

INT_WIDTHS = (8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
INT_RE = re.compile(r"^([iu])(8|16|32|64|128|256|512|1024|2048|4096|8192)$")
FLOAT_PRECISION_BITS = {"f32": 24, "f64": 53, "f128": 113}
FLOAT_RANK = {"f32": 0, "f64": 1, "f128": 2}
TYPE_ALIASES = {
    "int": "i64",
    "uint": "u64",
    "float": "f32",
    "double": "f64",
    "quad": "f128",
    "pointer": "ptr",
}


class ConversionKind(str, Enum):
    """Language-level classification of a source-to-destination conversion."""

    IDENTITY = "identity"
    LOSSLESS_WIDEN = "lossless_widen"
    CHECKED = "checked"
    EXPLICIT_LOSSY = "explicit_lossy"
    FORBIDDEN = "forbidden"


def canonical_type_name(type_name: str) -> str:
    """Return the canonical spelling used by the semantic type layer."""

    return TYPE_ALIASES.get(type_name.lower(), type_name)


def _int_shape(type_name: str) -> tuple[str, int] | None:
    match = INT_RE.match(type_name)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def _int_target_represents_source(
    source: tuple[str, int], target: tuple[str, int]
) -> bool:
    source_sign, source_width = source
    target_sign, target_width = target
    if source_sign == target_sign:
        return target_width >= source_width
    if target_sign == "i" and source_sign == "u":
        return target_width > source_width
    return False


def classify_conversion(source_type: str, target_type: str) -> ConversionKind:
    """Classify one language-level conversion without consulting a backend.

    Fixed-integer conversions that are not total remain implicitly legal only
    through the existing checked semantics.  Floating narrowing and mixed
    numeric conversions that can lose precision require an explicit cast.
    """

    source = canonical_type_name(source_type)
    target = canonical_type_name(target_type)
    if not source or not target:
        return ConversionKind.FORBIDDEN
    if source == target:
        return ConversionKind.IDENTITY

    source_int = _int_shape(source)
    target_int = _int_shape(target)
    if source_int is not None and target_int is not None:
        if _int_target_represents_source(source_int, target_int):
            return ConversionKind.LOSSLESS_WIDEN
        return ConversionKind.CHECKED

    if source in FLOAT_RANK and target in FLOAT_RANK:
        if FLOAT_RANK[target] > FLOAT_RANK[source]:
            return ConversionKind.LOSSLESS_WIDEN
        return ConversionKind.EXPLICIT_LOSSY

    if source_int is not None and target in FLOAT_PRECISION_BITS:
        sign, width = source_int
        exact_bits = width - 1 if sign == "i" else width
        if exact_bits <= FLOAT_PRECISION_BITS[target]:
            return ConversionKind.LOSSLESS_WIDEN
        return ConversionKind.EXPLICIT_LOSSY

    if source in FLOAT_PRECISION_BITS and target_int is not None:
        return ConversionKind.EXPLICIT_LOSSY

    return ConversionKind.FORBIDDEN


def is_implicit_conversion(kind: ConversionKind) -> bool:
    """Return whether normal AILang syntax may perform this conversion."""

    return kind in {
        ConversionKind.IDENTITY,
        ConversionKind.LOSSLESS_WIDEN,
        ConversionKind.CHECKED,
    }
