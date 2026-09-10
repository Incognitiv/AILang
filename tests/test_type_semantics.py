"""Regression tests for the canonical AILang conversion classifier."""

from type_semantics import ConversionKind, classify_conversion, is_implicit_conversion


def test_float_conversion_lattice() -> None:
    assert classify_conversion("float", "double") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("double", "quad") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("quad", "double") is ConversionKind.EXPLICIT_LOSSY
    assert classify_conversion("double", "float") is ConversionKind.EXPLICIT_LOSSY


def test_integer_conversion_policy_preserves_checked_narrowing() -> None:
    assert classify_conversion("i8", "i16") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("u8", "i16") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("i256", "i8") is ConversionKind.CHECKED
    assert classify_conversion("i8", "u64") is ConversionKind.CHECKED


def test_bool_to_integer_is_lossless_but_reverse_is_explicit() -> None:
    assert classify_conversion("bool", "i8") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("bool", "u8192") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("i8", "bool") is ConversionKind.EXPLICIT_LOSSY


def test_mixed_numeric_precision_policy() -> None:
    assert classify_conversion("i32", "double") is ConversionKind.LOSSLESS_WIDEN
    assert classify_conversion("i64", "double") is ConversionKind.EXPLICIT_LOSSY
    assert classify_conversion("u128", "quad") is ConversionKind.EXPLICIT_LOSSY
    assert classify_conversion("double", "i64") is ConversionKind.EXPLICIT_LOSSY


def test_non_numeric_cross_family_conversion_is_forbidden() -> None:
    assert classify_conversion("string", "i64") is ConversionKind.FORBIDDEN
    assert classify_conversion("bool", "string") is ConversionKind.FORBIDDEN
    assert classify_conversion("pointer", "ptr") is ConversionKind.IDENTITY


def test_only_safe_or_checked_conversions_are_implicit() -> None:
    assert is_implicit_conversion(ConversionKind.IDENTITY)
    assert is_implicit_conversion(ConversionKind.LOSSLESS_WIDEN)
    assert is_implicit_conversion(ConversionKind.CHECKED)
    assert not is_implicit_conversion(ConversionKind.EXPLICIT_LOSSY)
    assert not is_implicit_conversion(ConversionKind.FORBIDDEN)
