"""Golden tests for the first backend-neutral typed IR slice."""

import pytest
from ir import (
    Binary,
    Convert,
    IRLoweringError,
    Return,
    lower_numeric_binary_return,
    render_block,
)
from type_semantics import ConversionKind


def test_float_double_to_quad_materializes_both_widens() -> None:
    block = lower_numeric_binary_return("add", "float", "double", "quad")

    assert render_block(block) == "\n".join(
        [
            "%t0:f64 = convert.lossless_widen %arg0:f32",
            "%t1:f64 = add %t0, %arg1",
            "%t2:f128 = convert.lossless_widen %t1:f64",
            "ret %t2:f128",
        ]
    )
    first, binary, widen_return, returned = block.instructions
    assert isinstance(first, Convert)
    assert first.kind is ConversionKind.LOSSLESS_WIDEN
    assert isinstance(binary, Binary)
    assert binary.result.type_name == "f64"
    assert isinstance(widen_return, Convert)
    assert widen_return.kind is ConversionKind.LOSSLESS_WIDEN
    assert isinstance(returned, Return)
    assert returned.value.type_name == "f128"


def test_checked_integer_return_is_explicit_ir_node() -> None:
    block = lower_numeric_binary_return("add", "i256", "i256", "i8")

    assert render_block(block) == "\n".join(
        [
            "%t0:i256 = add %arg0, %arg1",
            "%t1:i8 = convert.checked %t0:i256",
            "ret %t1:i8",
        ]
    )
    conversion = block.instructions[1]
    assert isinstance(conversion, Convert)
    assert conversion.kind is ConversionKind.CHECKED


def test_lossy_float_return_never_becomes_implicit_ir() -> None:
    with pytest.raises(IRLoweringError, match="explicit conversion from f64 to f32"):
        lower_numeric_binary_return("add", "f64", "f64", "f32")


def test_mixed_integer_float_requires_lossless_numeric_join() -> None:
    with pytest.raises(IRLoweringError, match="no lossless numeric join"):
        lower_numeric_binary_return("add", "i64", "f64", "f64")


def test_binary_node_rejects_mismatched_operand_types() -> None:
    from ir import Value

    with pytest.raises(ValueError, match="operands must match"):
        Binary(
            operator="add",
            left=Value("%a", "f32"),
            right=Value("%b", "f64"),
            result=Value("%r", "f64"),
        )
