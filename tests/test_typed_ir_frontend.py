"""End-to-end tests from real AILang source through parser into typed IR."""

from parser.parser import Parser

import pytest
from ir import (
    Convert,
    FunctionIR,
    IRLoweringError,
    Value,
    lower_program,
    render_function,
)
from lexer.scan import tokenize
from type_semantics import ConversionKind


def _lower(source: str) -> FunctionIR:
    program = Parser(tokenize(source)).parse_program()
    functions = lower_program(program)
    assert len(functions) == 1
    return functions[0]


def test_real_ailang_float_double_to_quad_lowers_to_golden_ir() -> None:
    function = _lower("""
quad funkcja(float a, double b):
    return a + b
end
""")

    assert render_function(function) == "\n".join(
        [
            "func funkcja(%arg0:f32, %arg1:f64) -> f128",
            "%t0:f64 = convert.lossless_widen %arg0:f32",
            "%t1:f64 = add %t0, %arg1",
            "%t2:f128 = convert.lossless_widen %t1:f64",
            "ret %t2:f128",
        ]
    )


def test_ast_operand_order_survives_typed_ir_lowering() -> None:
    function = _lower("""
double swapped(double a, float b):
    return b + a
end
""")

    assert render_function(function) == "\n".join(
        [
            "func swapped(%arg0:f64, %arg1:f32) -> f64",
            "%t0:f64 = convert.lossless_widen %arg1:f32",
            "%t1:f64 = add %t0, %arg0",
            "ret %t1:f64",
        ]
    )


def test_frontend_fails_closed_on_unsupported_expression_shape() -> None:
    program = Parser(tokenize("""
double identity(double x):
    return x
end
""")).parse_program()

    with pytest.raises(IRLoweringError, match="requires a binary return"):
        lower_program(program)


def test_convert_node_cannot_lie_about_canonical_conversion_kind() -> None:
    with pytest.raises(ValueError, match="does not match canonical type semantics"):
        Convert(
            source=Value("%source", "f64"),
            result=Value("%result", "f32"),
            kind=ConversionKind.LOSSLESS_WIDEN,
        )


def test_identity_conversion_must_reuse_existing_value() -> None:
    with pytest.raises(ValueError, match="identity conversions"):
        Convert(
            source=Value("%source", "f64"),
            result=Value("%result", "f64"),
            kind=ConversionKind.IDENTITY,
        )
