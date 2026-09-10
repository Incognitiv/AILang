"""End-to-end tests from real AILang source through parser into typed IR."""

from parser.parser import Parser

import pytest
from ir import (
    Constant,
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


def test_contextual_unsuffixed_float_literal_stays_f32() -> None:
    function = _lower("""
float half(float x):
    return x / 2.0
end
""")

    assert render_function(function) == "\n".join(
        [
            "func half(%arg0:f32) -> f32",
            "%t0:f32 = const.float 2.0",
            "%t1:f32 = div %arg0, %t0",
            "ret %t1:f32",
        ]
    )


def test_explicit_double_literal_widens_f32_operand() -> None:
    function = _lower("""
double half64(float x):
    return x / 2.0d
end
""")

    assert render_function(function) == "\n".join(
        [
            "func half64(%arg0:f32) -> f64",
            "%t0:f64 = const.float 2.0",
            "%t1:f64 = convert.lossless_widen %arg0:f32",
            "%t2:f64 = div %t1, %t0",
            "ret %t2:f64",
        ]
    )


def test_exact_f128_literal_lexeme_survives_parser_and_typed_ir() -> None:
    function = _lower("""
quad exact_quad():
    return 1.234567890123456789012345678901234q
end
""")

    assert render_function(function) == "\n".join(
        [
            "func exact_quad() -> f128",
            "%t0:f128 = const.float 1.234567890123456789012345678901234",
            "ret %t0:f128",
        ]
    )


def test_typed_locals_and_nested_expressions_lower_to_ssa() -> None:
    function = _lower("""
double compute(float a, double b):
    float half = a / 2.0
    double mixed = (half + b) * 4.0
    return mixed + b
end
""")

    assert render_function(function) == "\n".join(
        [
            "func compute(%arg0:f32, %arg1:f64) -> f64",
            "%t0:f32 = const.float 2.0",
            "%t1:f32 = div %arg0, %t0",
            "%t2:f64 = convert.lossless_widen %t1:f32",
            "%t3:f64 = add %t2, %arg1",
            "%t4:f64 = const.float 4.0",
            "%t5:f64 = mul %t3, %t4",
            "%t6:f64 = add %t5, %arg1",
            "ret %t6:f64",
        ]
    )


def test_typed_local_boundary_materializes_checked_integer_conversion() -> None:
    function = _lower("""
i8 narrow_local(i256 a, i256 b):
    i8 value = a + b
    return value
end
""")

    assert render_function(function) == "\n".join(
        [
            "func narrow_local(%arg0:i256, %arg1:i256) -> i8",
            "%t0:i256 = add %arg0, %arg1",
            "%t1:i8 = convert.checked %t0:i256",
            "ret %t1:i8",
        ]
    )


def test_local_shadowing_fails_closed() -> None:
    program = Parser(tokenize("""
double shadow(double x):
    double x = 2.0
    return x
end
""")).parse_program()

    with pytest.raises(IRLoweringError, match="does not allow local shadowing"):
        lower_program(program)


def test_frontend_fails_closed_on_unsupported_expression_shape() -> None:
    program = Parser(tokenize("""
double unsupported(double x):
    return sqrt(x)
end
""")).parse_program()

    with pytest.raises(IRLoweringError, match="does not support expression"):
        lower_program(program)


def test_constant_node_rejects_mismatched_literal_family() -> None:
    with pytest.raises(ValueError, match="integer IR constant requires"):
        Constant(literal_kind="int", value_text="2", result=Value("%c", "f64"))


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
