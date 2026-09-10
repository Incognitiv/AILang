"""Tests for the stable data-only Typed IR certificate format."""

from parser.parser import Parser

from ir import lower_program, serialize_function_certificate
from lexer.scan import tokenize


def _certificate(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    (function,) = lower_program(program)
    return serialize_function_certificate(function)


def test_real_ailang_program_serializes_complete_typed_ir_certificate() -> None:
    certificate = _certificate("""
quad funkcja(float a, double b):
    return a + b
end
""")

    assert certificate == "\n".join(
        [
            "AILANG_TYPED_IR_CERTIFICATE\t1",
            "F\tfunkcja\tf128",
            "P\t%arg0\tf32",
            "P\t%arg1\tf64",
            "C\t%arg0\tf32\t%t0\tf64\tlossless_widen",
            "B\tadd\t%t0\tf64\t%arg1\tf64\t%t1\tf64",
            "C\t%t1\tf64\t%t2\tf128\tlossless_widen",
            "R\t%t2\tf128",
            "",
        ]
    )


def test_certificate_preserves_noncommutative_operand_order() -> None:
    certificate = _certificate("""
double subtract(double a, float b):
    return b - a
end
""")

    assert "B\tsub\t%t0\tf64\t%arg0\tf64\t%t1\tf64" in certificate
