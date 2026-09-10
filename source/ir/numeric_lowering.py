"""Typed-IR lowering primitives for fixed numeric values."""

from __future__ import annotations

from type_semantics import (
    ConversionKind,
    canonical_type_name,
    classify_conversion,
    is_implicit_conversion,
    join_numeric_types,
)

from .model import Binary, Block, Convert, Instruction, Return, Value


class IRLoweringError(TypeError):
    """The frontend contract cannot be represented by this typed-IR slice."""


def fresh_value(instructions: list[Instruction], type_name: str) -> Value:
    """Allocate the next deterministic SSA temporary."""

    return Value(f"%t{len(instructions)}", canonical_type_name(type_name))


def coerce_value(
    value: Value,
    target_type: str,
    instructions: list[Instruction],
) -> Value:
    """Materialize one canonical implicit conversion, or fail closed."""

    target = canonical_type_name(target_type)
    kind = classify_conversion(value.type_name, target)
    if kind is ConversionKind.IDENTITY:
        return value
    if not is_implicit_conversion(kind):
        raise IRLoweringError(
            f"typed IR requires an explicit conversion from "
            f"{value.type_name} to {target}"
        )

    result = fresh_value(instructions, target)
    instructions.append(Convert(source=value, result=result, kind=kind))
    return result


def emit_numeric_binary(
    operator: str,
    left: Value,
    right: Value,
    instructions: list[Instruction],
) -> Value:
    """Emit one numeric binary instruction after lossless operand joining."""

    joined = join_numeric_types(left.type_name, right.type_name)
    if joined is None:
        raise IRLoweringError(
            f"no lossless numeric join for {left.type_name} and {right.type_name}"
        )

    typed_left = coerce_value(left, joined, instructions)
    typed_right = coerce_value(right, joined, instructions)
    result = fresh_value(instructions, joined)
    instructions.append(
        Binary(operator=operator, left=typed_left, right=typed_right, result=result)
    )
    return result


def lower_numeric_binary_values(
    operator: str,
    left: Value,
    right: Value,
    return_type: str,
) -> Block:
    """Lower typed operands, preserving their actual SSA/source identities."""

    instructions: list[Instruction] = []
    result = emit_numeric_binary(operator, left, right, instructions)
    returned = coerce_value(result, return_type, instructions)
    instructions.append(Return(value=returned))
    return Block(instructions=tuple(instructions))


def lower_numeric_binary_return(
    operator: str,
    left_type: str,
    right_type: str,
    return_type: str,
) -> Block:
    """Convenience wrapper for tests and callers that only have operand types."""

    left = Value("%arg0", canonical_type_name(left_type))
    right = Value("%arg1", canonical_type_name(right_type))
    return lower_numeric_binary_values(operator, left, right, return_type)
