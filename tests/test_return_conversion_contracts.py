"""Frontend contract tests for explicit return-type conversions."""

from parser.parser import Parser

import pytest
from lexer.scan import tokenize


def _parse(source: str) -> None:
    Parser(tokenize(source)).parse_program()


def test_quad_return_accepts_lossless_float_promotion_chain() -> None:
    _parse("""
quad funkcja(float a, double b):
    return a + b
end
""")


def test_lossless_float_return_widening_is_implicit() -> None:
    _parse("""
double widen(float x):
    return x
end
""")


def test_unsuffixed_float_literal_is_contextual() -> None:
    _parse("""
float half(float x):
    return x / 2.0
end
""")


def test_explicit_double_literal_preserves_f64_precision() -> None:
    with pytest.raises(SyntaxError, match="explicit lossy conversion"):
        _parse("""
float half(float x):
    return x / 2.0d
end
""")


def test_float_return_narrowing_requires_explicit_conversion() -> None:
    with pytest.raises(SyntaxError, match="explicit lossy conversion"):
        _parse("""
float narrow(double x):
    return x
end
""")


def test_integer_return_narrowing_keeps_checked_semantics() -> None:
    _parse("""
i8 checked(i256 x):
    return x
end
""")


def test_bool_return_widens_losslessly_to_integer() -> None:
    _parse("""
i64 status(i64 x):
    return x > 0
end
""")


def test_cross_family_return_is_forbidden() -> None:
    with pytest.raises(SyntaxError, match="cannot convert"):
        _parse("""
i64 bad(string x):
    return x
end
""")
