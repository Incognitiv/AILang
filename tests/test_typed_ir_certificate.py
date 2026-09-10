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
            "AILANG_TYPED_IR_CERTIFICATE\t2",
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


def test_certificate_serializes_contextual_constant_and_nested_ssa() -> None:
    certificate = _certificate("""
float scaled(float x):
    float half = x / 2.0
    return half * 4.0
end
""")

    assert "K\t%t0\tf32\tfloat\t2.0" in certificate
    assert "B\tdiv\t%arg0\tf32\t%t0\tf32\t%t1\tf32" in certificate
    assert "K\t%t2\tf32\tfloat\t4.0" in certificate
    assert "B\tmul\t%t1\tf32\t%t2\tf32\t%t3\tf32" in certificate
    assert certificate.endswith("R\t%t3\tf32\n")


def test_certificate_preserves_exact_f128_literal_text() -> None:
    certificate = _certificate("""
quad exact_quad():
    return 1.234567890123456789012345678901234q
end
""")

    assert (
        "K\t%t0\tf128\tfloat\t1.234567890123456789012345678901234\n"
        in certificate
    )
    assert certificate.endswith("R\t%t0\tf128\n")
