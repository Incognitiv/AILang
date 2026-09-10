"""Frontend contract tests for explicit return-type conversions."""

import pytest

from lexer.scan import tokenize
from parser.parser import Parser


def _parse(source: str) -> None:
    Parser(tokenize(source)).parse_program()


def test_quad_return_accepts_lossless_float_promotion_chain() -> None:
    _parse(
        """
quad funkcja(float a, double b):
    return a + b
end
"""
    )


def test_lossless_float_return_widening_is_implicit() -> None:
    _parse(
        """
double widen(float x):
    return x
end
"""
    )


def test_float_return_narrowing_requires_explicit_conversion() -> None:
    with pytest.raises(SyntaxError, match="explicit lossy conversion"):
        _parse(
            """
float narrow(double x):
    return x
end
"""
        )


def test_integer_return_narrowing_keeps_checked_semantics() -> None:
    _parse(
        """
i8 checked(i256 x):
    return x
end
"""
    )


def test_cross_family_return_is_forbidden() -> None:
    with pytest.raises(SyntaxError, match="cannot convert"):
        _parse(
            """
i64 bad(string x):
    return x
end
"""
        )
